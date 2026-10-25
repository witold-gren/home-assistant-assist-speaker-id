"""Talking to wyoming-speaker-id: Wyoming (describe, transcribe) and the HTTP API."""

from __future__ import annotations

from collections.abc import AsyncIterable
from typing import Any

import aiohttp
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.info import Describe, Info

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
SAMPLE_CHANNELS = 1
TIMEOUT = 10


class SpeakerIdError(Exception):
    """The service is unreachable or answered with an error."""


class SpeakerIdClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        api_port: int,
        token: str | None = None,
    ) -> None:
        self.session = session
        self.host = host
        self.port = port
        self.api_port = api_port
        self.token = token or None

    # --- Wyoming ---

    async def describe(self) -> Info:
        try:
            async with AsyncTcpClient(self.host, self.port) as client:
                await client.write_event(Describe().event())
                while True:
                    event = await client.read_event()
                    if event is None:
                        raise SpeakerIdError("connection closed during describe")
                    if Info.is_type(event.type):
                        return Info.from_event(event)
        except OSError as err:
            raise SpeakerIdError(f"cannot connect to {self.host}:{self.port}: {err}") from err

    async def transcribe(
        self, language: str, stream: AsyncIterable[bytes]
    ) -> tuple[str, dict[str, Any]] | None:
        """Text and the full transcript event data (with speaker fields); None if lost."""
        async with AsyncTcpClient(self.host, self.port) as client:
            await client.write_event(Transcribe(language=language).event())
            await client.write_event(
                AudioStart(rate=SAMPLE_RATE, width=SAMPLE_WIDTH, channels=SAMPLE_CHANNELS).event()
            )
            async for audio in stream:
                await client.write_event(
                    AudioChunk(
                        rate=SAMPLE_RATE, width=SAMPLE_WIDTH, channels=SAMPLE_CHANNELS, audio=audio
                    ).event()
                )
            await client.write_event(AudioStop().event())
            while True:
                event = await client.read_event()
                if event is None:
                    return None
                if Transcript.is_type(event.type):
                    return Transcript.from_event(event).text, dict(event.data or {})

    # --- HTTP API ---

    async def last_audio(self) -> tuple[bytes, float]:
        """The last utterance as WAV and its age in seconds (LookupError: none recent)."""
        info = await self.api("GET", "last")
        wav = await self.api("GET", "last.wav", raw=True)
        return wav, float(info.get("age_seconds", 0.0))

    async def api(self, method: str, path: str, json: Any = None, raw: bool = False) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        url = f"http://{self.host}:{self.api_port}/api/{path}"
        try:
            async with self.session.request(
                method, url, json=json, headers=headers, timeout=aiohttp.ClientTimeout(TIMEOUT)
            ) as resp:
                if resp.status == 404 and path.startswith("last"):
                    raise LookupError("no utterance in the last 10 minutes")
                if resp.status >= 400:
                    raise SpeakerIdError(f"{method} {path}: HTTP {resp.status} {await resp.text()}")
                return await resp.read() if raw else await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SpeakerIdError(f"{method} {path}: {err}") from err
