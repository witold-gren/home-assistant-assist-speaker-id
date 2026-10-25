"""Wyoming connection handler: transparent ASR proxy + speaker identification.

Every event from the client goes to the upstream ASR server unchanged and every
upstream event comes back unchanged - only `transcript` gets extra data fields
(`speaker`, `speaker_name`, `speaker_score`, `speaker_scores`, `speech_seconds`,
`enrollment`) and a `speaker` entry in its `context`. Clients that do not know
these fields (Home Assistant's stock Wyoming STT) simply ignore them.

The embedding is computed in a thread while the upstream server transcribes,
so identification adds almost no latency.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
from typing import Any

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncClient
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler

from . import __version__
from .audio import utterance
from .service import SpeakerService

_LOGGER = logging.getLogger(__name__)

UPSTREAM_TIMEOUT = 60.0
NAME_SUFFIX = " + speaker-id"


def describe_info(upstream: Event) -> Event:
    """Upstream info with the ASR programs marked as speaker-identifying."""
    data = copy.deepcopy(upstream.data or {})
    for program in data.get("asr", []):
        if not program.get("name", "").endswith(NAME_SUFFIX):
            program["name"] = f"{program.get('name', 'asr')}{NAME_SUFFIX}"
        program["description"] = (
            f"{program.get('description') or program.get('name')} - with speaker "
            f"identification (wyoming-speaker-id {__version__})"
        )
    return Event(type=upstream.type, data=data, payload=upstream.payload)


class SpeakerIdHandler(AsyncEventHandler):
    def __init__(self, reader, writer, service: SpeakerService) -> None:
        super().__init__(reader, writer)
        self.service = service
        self._upstream: AsyncClient | None = None
        self._pcm = bytearray()
        self._format: tuple[int, int, int] = (16000, 2, 1)

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(await self._describe())
            return True
        if Transcribe.is_type(event.type) or (
            AudioStart.is_type(event.type) and self._upstream is None
        ):
            await self._open_upstream()
            self._pcm.clear()
        if AudioStart.is_type(event.type):
            start = AudioStart.from_event(event)
            self._format = (start.rate, start.width, start.channels)
        elif AudioChunk.is_type(event.type):
            chunk = AudioChunk.from_event(event)
            self._format = (chunk.rate, chunk.width, chunk.channels)
            self._pcm.extend(chunk.audio)
        if self._upstream is None:
            _LOGGER.debug("Ignoring %s outside a transcription", event.type)
            return True
        await self._upstream.write_event(event)
        if AudioStop.is_type(event.type):
            await self._finish()
        return True

    async def disconnect(self) -> None:
        await self._close_upstream()

    # --- upstream ---

    async def _open_upstream(self) -> None:
        await self._close_upstream()
        self._upstream = AsyncClient.from_uri(self.service.settings.upstream)
        await self._upstream.connect()

    async def _close_upstream(self) -> None:
        if self._upstream is not None:
            upstream, self._upstream = self._upstream, None
            with contextlib.suppress(OSError):
                await upstream.disconnect()

    async def _describe(self) -> Event:
        async with self.service.info_lock:
            cached = self.service.cached_info()
            if cached is not None:
                return cached
            client = AsyncClient.from_uri(self.service.settings.upstream)
            await client.connect()
            try:
                await client.write_event(Describe().event())
                while True:
                    event = await asyncio.wait_for(client.read_event(), UPSTREAM_TIMEOUT)
                    if event is None:
                        raise ConnectionError("upstream closed during describe")
                    if Info.is_type(event.type):
                        break
            finally:
                await client.disconnect()
            info = describe_info(event)
            self.service.cache_info(info)
            return info

    async def _finish(self) -> None:
        """Identify the speaker while upstream transcribes; relay its events."""
        rate, width, channels = self._format
        pcm = bytes(self._pcm)
        self._pcm.clear()
        loop = asyncio.get_running_loop()
        settings = self.service.settings

        def analyse():
            utt = utterance(pcm, rate, width, channels, settings.max_speech)
            return utt, *self.service.analyse(utt)

        analysis = loop.run_in_executor(None, analyse)
        upstream = self._upstream
        assert upstream is not None
        try:
            while True:
                event = await asyncio.wait_for(upstream.read_event(), UPSTREAM_TIMEOUT)
                if event is None:
                    raise ConnectionError("upstream closed before transcript")
                if not Transcript.is_type(event.type):
                    await self.write_event(event)  # streaming chunks, errors, ...
                    if event.type == "error":
                        return
                    continue
                text = Transcript.from_event(event).text
                try:
                    utt, embedding, result = await analysis
                    extra = await loop.run_in_executor(
                        None, self.service.finish, utt, embedding, result, text
                    )
                except Exception:  # identification must never break transcription
                    _LOGGER.exception("Speaker identification failed")
                    extra = {"speaker": None, "speaker_reason": "error"}
                await self.write_event(augment(event, extra))
                return
        finally:
            await self._close_upstream()


def augment(event: Event, extra: dict[str, Any]) -> Event:
    data = dict(event.data or {})
    data.update(extra)
    context = dict(data.get("context") or {})
    context["speaker"] = {k: v for k, v in extra.items() if k != "enrollment"}
    data["context"] = context
    return Event(type=event.type, data=data, payload=event.payload)
