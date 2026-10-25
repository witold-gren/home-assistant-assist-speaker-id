"""A real wyoming-speaker-id (fake embedder, fake upstream ASR) on localhost."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path
import socket

from aiohttp import web
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from wyoming.server import AsyncTcpServer

from custom_components.speaker_id.const import CONF_API_PORT, DOMAIN

# wyoming_speaker_id i tests_service: pythonpath w pytest.ini
from tests_service.conftest import FakeEmbedder, pcm, voice
from tests_service.test_handler import FakeAsr
from wyoming_speaker_id.handler import SpeakerIdHandler
from wyoming_speaker_id.http_api import build_app
from wyoming_speaker_id.service import Settings, SpeakerService
from wyoming_speaker_id.speakers import SpeakerStore

__all__ = ["pcm", "voice"]


@pytest.fixture(autouse=True)
async def auto_enable_custom_integrations(hass, enable_custom_integrations):
    # conversation needs entity exposure from the homeassistant component
    assert await async_setup_component(hass, "homeassistant", {})
    yield


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class Stack:
    service: SpeakerService
    port: int
    api_port: int

    def enroll(self, speaker: str, name: str, pitch: float) -> None:
        self.service.store.upsert(speaker, name, None)
        for seed in range(3):
            embedding = self.service.embedder.embed(voice(pitch, seed=seed))
            self.service.store.add_sample(speaker, embedding, 2.0)


@pytest.fixture
async def stack(tmp_path: Path, socket_enabled) -> AsyncIterator[Stack]:
    FakeAsr.received, FakeAsr.audio_bytes, FakeAsr.streaming = [], 0, False
    up, port, api_port = free_port(), free_port(), free_port()
    store = SpeakerStore(tmp_path / "speakers.json", threshold=0.8, margin=0.05)
    service = SpeakerService(Settings(upstream=f"tcp://127.0.0.1:{up}"), store, FakeEmbedder())
    upstream = AsyncTcpServer("127.0.0.1", up)
    proxy = AsyncTcpServer("127.0.0.1", port)
    await upstream.start(FakeAsr)
    await proxy.start(partial(SpeakerIdHandler, service=service))
    runner = web.AppRunner(build_app(service, token=None))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", api_port).start()
    yield Stack(service, port, api_port)
    await runner.cleanup()
    await proxy.stop()
    await upstream.stop()


@pytest.fixture
def entry(stack: Stack) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Assist Speaker ID",
        unique_id=f"127.0.0.1:{stack.port}",
        data={"host": "127.0.0.1", "port": stack.port, CONF_API_PORT: stack.api_port},
    )
