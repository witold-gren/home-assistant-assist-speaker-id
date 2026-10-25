"""Wyoming proxy end to end: client -> proxy -> fake upstream ASR, over real TCP."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from functools import partial
import socket
from typing import ClassVar

import pytest
from wyoming.asr import Transcribe, Transcript, TranscriptChunk, TranscriptStart
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.event import Event
from wyoming.info import AsrModel, AsrProgram, Attribution, Describe, Info
from wyoming.server import AsyncEventHandler, AsyncTcpServer

from wyoming_speaker_id.handler import NAME_SUFFIX, SpeakerIdHandler
from wyoming_speaker_id.service import SpeakerService

from .conftest import pcm, voice

ATTR = Attribution(name="test", url="https://example.invalid")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeAsr(AsyncEventHandler):
    """Upstream: remembers received audio, answers with a fixed transcript."""

    received: ClassVar[list[str]] = []
    audio_bytes = 0
    streaming = False

    async def handle_event(self, event: Event) -> bool:
        FakeAsr.received.append(event.type)
        if Describe.is_type(event.type):
            info = Info(asr=[AsrProgram(
                name="faster-whisper", attribution=ATTR, installed=True, version="1",
                description="Whisper", models=[AsrModel(
                    name="small", attribution=ATTR, installed=True, version="1",
                    description="small", languages=["pl"])])])  # fmt: skip
            await self.write_event(info.event())
        elif AudioChunk.is_type(event.type):
            FakeAsr.audio_bytes += len(AudioChunk.from_event(event).audio)
        elif AudioStop.is_type(event.type):
            if FakeAsr.streaming:
                await self.write_event(TranscriptStart(language="pl").event())
                await self.write_event(TranscriptChunk(text="jakie mam").event())
            await self.write_event(Transcript(text="jakie mam dzisiaj zajęcia").event())
        return True


@pytest.fixture
async def proxy(service: SpeakerService) -> AsyncIterator[int]:
    FakeAsr.received, FakeAsr.audio_bytes, FakeAsr.streaming = [], 0, False
    up_port, proxy_port = free_port(), free_port()
    upstream = AsyncTcpServer("127.0.0.1", up_port)
    server = AsyncTcpServer("127.0.0.1", proxy_port)
    service.settings.upstream = f"tcp://127.0.0.1:{up_port}"
    await upstream.start(FakeAsr)
    await server.start(partial(SpeakerIdHandler, service=service))
    yield proxy_port
    await server.stop()
    await upstream.stop()


async def transcribe(port: int, audio: bytes) -> list[Event]:
    """What Home Assistant's Wyoming STT does; returns events until the transcript."""
    async with AsyncTcpClient("127.0.0.1", port) as client:
        await client.write_event(Transcribe(language="pl").event())
        await client.write_event(AudioStart(rate=16000, width=2, channels=1).event())
        for i in range(0, len(audio), 2048):
            await client.write_event(
                AudioChunk(rate=16000, width=2, channels=1, audio=audio[i : i + 2048]).event()
            )
        await client.write_event(AudioStop().event())
        events = []
        while True:
            event = await asyncio.wait_for(client.read_event(), 5)
            assert event is not None
            events.append(event)
            if Transcript.is_type(event.type):
                return events


async def test_transcript_passes_through_with_speaker(proxy: int, service: SpeakerService) -> None:
    service.store.upsert("kasia", "Kasia", None)
    service.store.add_sample("kasia", service.embedder.embed(voice(300)), 2.0)
    audio = pcm(voice(300, seed=3))

    events = await transcribe(proxy, audio)

    final = events[-1]
    assert Transcript.from_event(final).text == "jakie mam dzisiaj zajęcia"
    assert final.data["speaker"] == "kasia"
    assert final.data["speaker_name"] == "Kasia"
    assert final.data["speaker_score"] > 0.9
    assert final.data["context"]["speaker"]["speaker"] == "kasia"
    # upstream got the whole audio and the same events
    assert FakeAsr.audio_bytes == len(audio)
    assert FakeAsr.received[:2] == ["transcribe", "audio-start"]
    assert FakeAsr.received[-1] == "audio-stop"


async def test_unknown_speaker_still_transcribes(proxy: int, service: SpeakerService) -> None:
    events = await transcribe(proxy, pcm(voice(300)))
    assert events[-1].data["text"] == "jakie mam dzisiaj zajęcia"
    assert events[-1].data["speaker"] is None


async def test_short_speech_is_unknown_without_embedding(
    proxy: int, service: SpeakerService
) -> None:
    service.store.upsert("kasia", "Kasia", None)
    service.store.add_sample("kasia", service.embedder.embed(voice(300)), 2.0)
    calls = service.embedder.calls

    events = await transcribe(proxy, pcm(voice(300, seconds=0.5)))

    assert events[-1].data["speaker"] is None
    assert "too short" in events[-1].data["speaker_reason"]
    assert service.embedder.calls == calls


async def test_streaming_events_are_relayed(proxy: int) -> None:
    FakeAsr.streaming = True
    events = await transcribe(proxy, pcm(voice(300)))
    assert [e.type for e in events] == ["transcript-start", "transcript-chunk", "transcript"]


async def test_describe_marks_program(proxy: int) -> None:
    async with AsyncTcpClient("127.0.0.1", proxy) as client:
        await client.write_event(Describe().event())
        event = await asyncio.wait_for(client.read_event(), 5)
    info = Info.from_event(event)
    assert info.asr[0].name == "faster-whisper" + NAME_SUFFIX
    assert info.asr[0].models[0].languages == ["pl"]


async def test_enrollment_takes_next_utterances(proxy: int, service: SpeakerService) -> None:
    from wyoming_speaker_id.service import Enrollment

    service.store.upsert("jan", "Jan", None)
    service.enrollment = Enrollment("jan", 2)

    first = (await transcribe(proxy, pcm(voice(180, seed=1))))[-1]
    short = (await transcribe(proxy, pcm(voice(180, seconds=0.8))))[-1]  # too short
    second = (await transcribe(proxy, pcm(voice(180, seed=2))))[-1]

    assert first.data["enrollment"] == {"speaker": "jan", "count": 2, "done": 1}
    assert short.data["enrollment"]["done"] == 1
    assert second.data["enrollment"]["done"] == 2
    assert service.enrollment is None
    assert len(service.store.speakers["jan"].samples) == 2
    after = (await transcribe(proxy, pcm(voice(180, seed=4))))[-1]
    assert after.data["speaker"] == "jan"
    assert "enrollment" not in after.data
