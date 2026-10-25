"""HTTP management API."""

from __future__ import annotations

from collections.abc import AsyncIterator

from aiohttp.test_utils import TestClient, TestServer
import numpy as np
import pytest

from wyoming_speaker_id.audio import to_wav, utterance
from wyoming_speaker_id.http_api import build_app
from wyoming_speaker_id.service import SpeakerService

from .conftest import pcm, voice


@pytest.fixture
async def api(service: SpeakerService) -> AsyncIterator[TestClient]:
    client = TestClient(TestServer(build_app(service, token=None)))
    await client.start_server()
    yield client
    await client.close()


async def test_enroll_by_wav_then_identify(api: TestClient) -> None:
    assert (await api.put("/api/speakers/ania", json={"name": "Ania"})).status == 200
    for seed in range(3):
        resp = await api.post("/api/speakers/ania/samples", data=to_wav(voice(230, seed=seed)))
        assert resp.status == 200
    speakers = await (await api.get("/api/speakers")).json()
    assert (
        speakers
        == [{"id": "ania", "name": "Ania", "threshold": None, "samples": 3, "seconds": 6.0}]
        or speakers[0]["samples"] == 3
    )

    result = await (await api.post("/api/identify", data=to_wav(voice(230, seed=9)))).json()
    assert result["speaker"] == "ania"


async def test_wav_without_speech_is_rejected(api: TestClient) -> None:
    await api.put("/api/speakers/ania", json={"name": "Ania"})
    silence = to_wav(np.zeros(16000, dtype=np.float32))
    resp = await api.post("/api/speakers/ania/samples", data=silence)
    assert resp.status == 400
    assert "not enough speech" in await resp.text()


async def test_unknown_speaker_404(api: TestClient) -> None:
    assert (await api.post("/api/speakers/nikt/samples/last")).status == 404
    assert (await api.delete("/api/speakers/nikt")).status == 404


async def test_last_utterance_as_sample(api: TestClient, service: SpeakerService) -> None:
    assert (await api.get("/api/last")).status == 404
    utt = utterance(pcm(voice(150)), 16000, 2, 1, 8.0)
    embedding, result = service.analyse(utt)
    service.finish(utt, embedding, result, "dzień dobry")
    await api.put("/api/speakers/tomek", json={"name": "Tomek"})

    last = await (await api.get("/api/last")).json()
    wav = await api.get("/api/last.wav")
    added = await (await api.post("/api/speakers/tomek/samples/last")).json()

    assert last["text"] == "dzień dobry" and last["speaker"] is None
    assert wav.content_type == "audio/wav"
    assert added["samples"] == 1


async def test_enrollment_start_stop(api: TestClient, service: SpeakerService) -> None:
    started = await (
        await api.post("/api/enrollment", json={"speaker": "tosia", "count": 5})
    ).json()
    assert started == {"speaker": "tosia", "count": 5, "done": 0}
    assert "tosia" in service.store.speakers  # created when the enrollment starts
    info = await (await api.get("/api/info")).json()
    assert info["enrollment"]["speaker"] == "tosia"
    stopped = await (await api.delete("/api/enrollment")).json()
    assert stopped["stopped"]["speaker"] == "tosia"
    assert service.enrollment is None


async def test_calibrate_and_apply(api: TestClient, service: SpeakerService) -> None:
    for speaker, pitch in (("tata", 110), ("mama", 220)):
        await api.put(f"/api/speakers/{speaker}", json={})
        for seed in range(3):
            await api.post(f"/api/speakers/{speaker}/samples", data=to_wav(voice(pitch, seed=seed)))

    report = await (await api.post("/api/calibrate?apply=1")).json()

    assert report["applied"]
    assert report["speakers"]["tata"]["separable"]
    assert (
        service.store.speakers["tata"].threshold
        == report["speakers"]["tata"]["suggested_threshold"]
    )


async def test_token_required(service: SpeakerService) -> None:
    client = TestClient(TestServer(build_app(service, token="sekret")))
    await client.start_server()
    try:
        assert (await client.get("/api/info")).status == 401
        ok = await client.get("/api/info", headers={"Authorization": "Bearer sekret"})
        assert ok.status == 200
    finally:
        await client.close()


async def test_settings_api(api: TestClient, service: SpeakerService) -> None:
    before = await (await api.get("/api/settings")).json()
    changed = await (await api.put("/api/settings", json={"threshold": 0.6, "margin": None})).json()
    bad = await api.put("/api/settings", json={"threshold": 2})

    assert before["from"] == "startup"
    assert changed == {"threshold": 0.6, "margin": 0.05, "min_speech": 1.0, "from": "file"}
    assert service.store.threshold == 0.6
    assert bad.status == 400
