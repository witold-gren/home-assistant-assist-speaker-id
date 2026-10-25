"""Voiceprints: identification (threshold, margin, unknown), persistence, calibration."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from wyoming_speaker_id.speakers import SpeakerStore

from .conftest import FakeEmbedder, voice

EMB = FakeEmbedder()


def enroll(store: SpeakerStore, speaker: str, pitch: float, count: int = 4) -> None:
    store.upsert(speaker, speaker.title(), None)
    for seed in range(count):
        store.add_sample(speaker, EMB.embed(voice(pitch, seed=seed)), 2.0)


def test_no_speakers_is_unknown(store: SpeakerStore) -> None:
    result = store.identify(EMB.embed(voice(120)))
    assert result.speaker is None
    assert result.reason == "no enrolled speakers"


def test_identifies_the_right_speaker(store: SpeakerStore) -> None:
    enroll(store, "tata", 110)
    enroll(store, "mama", 220)

    low = store.identify(EMB.embed(voice(110, seed=99)))
    high = store.identify(EMB.embed(voice(220, seed=99)))

    assert (low.speaker, low.name) == ("tata", "Tata")
    assert high.speaker == "mama"
    assert low.scores["tata"] > low.scores["mama"]


def test_stranger_below_threshold_is_unknown(store: SpeakerStore) -> None:
    enroll(store, "tata", 110)
    enroll(store, "mama", 220)

    result = store.identify(EMB.embed(voice(700)))

    assert result.speaker is None
    assert "below threshold" in result.reason


def test_too_close_to_runner_up_is_unknown(store: SpeakerStore) -> None:
    enroll(store, "kasia", 300)
    enroll(store, "tosia", 302)  # siblings: almost the same voice
    store.margin = 0.2

    result = store.identify(EMB.embed(voice(301)))

    assert result.speaker is None
    assert "too close" in result.reason


def test_per_speaker_threshold(store: SpeakerStore) -> None:
    enroll(store, "tata", 110)
    store.upsert("tata", None, 1.0)  # unreachable for the similarity (cosine < 1)

    assert store.identify(EMB.embed(voice(110, seed=7))).speaker is None


def test_first_sample_has_no_score_next_ones_do(store: SpeakerStore) -> None:
    store.upsert("tata", "Tata", None)
    assert store.add_sample("tata", EMB.embed(voice(110)), 2.0) is None
    score = store.add_sample("tata", EMB.embed(voice(110, seed=1)), 2.0)
    assert score is not None and score > 0.9


def test_persistence_roundtrip(store: SpeakerStore) -> None:
    enroll(store, "tata", 110)

    again = SpeakerStore(store.path, threshold=0.8, margin=0.05)

    assert again.speakers["tata"].name == "Tata"
    assert len(again.speakers["tata"].samples) == 4
    assert again.identify(EMB.embed(voice(110, seed=5))).speaker == "tata"
    assert "embedding" in store.path.read_text()  # vectors, not recordings


def test_delete_speaker_and_sample(store: SpeakerStore) -> None:
    enroll(store, "tata", 110, count=2)
    assert store.delete_sample("tata", 0)
    assert len(store.speakers["tata"].samples) == 1
    assert store.delete("tata")
    assert not store.delete("tata")


def test_invalid_speaker_id(store: SpeakerStore) -> None:
    with pytest.raises(ValueError):
        store.upsert("Łucja Żak", None, None)


def test_calibration_suggests_threshold_between_own_and_others(store: SpeakerStore) -> None:
    enroll(store, "tata", 110)
    enroll(store, "mama", 220)
    store.upsert("jan", "Jan", None)
    store.add_sample("jan", EMB.embed(voice(400)), 2.0)

    report = store.calibrate()

    tata = report["speakers"]["tata"]
    assert tata["separable"]
    assert tata["others_max"] < tata["suggested_threshold"] < tata["own_min"]
    assert "needs at least" in report["speakers"]["jan"]["note"]

    store.apply_calibration(report)
    assert store.speakers["tata"].threshold == tata["suggested_threshold"]
    assert store.speakers["jan"].threshold is None


def test_voiceprint_is_normalised_mean(store: SpeakerStore) -> None:
    enroll(store, "tata", 110, count=3)
    vp = store.speakers["tata"].voiceprint()
    assert vp is not None
    assert np.isclose(np.linalg.norm(vp), 1.0)


def test_settings_persist_and_win_over_startup(store: SpeakerStore) -> None:
    enroll(store, "tata", 110)
    assert store.settings_from == "startup"

    store.set_settings(threshold=0.6, margin=0.1, min_speech=0.8)
    again = SpeakerStore(store.path, threshold=0.5, margin=0.05, min_speech=1.0)

    assert again.settings() == {"threshold": 0.6, "margin": 0.1, "min_speech": 0.8}
    assert again.settings_from == "file"


@pytest.mark.parametrize(
    "kwargs", [{"threshold": 1.5}, {"margin": 0.9}, {"min_speech": 0.1}, {"threshold": -0.1}]
)
def test_settings_ranges(store: SpeakerStore, kwargs: dict) -> None:
    with pytest.raises(ValueError):
        store.set_settings(**kwargs)


# --- recordings ---


def test_recordings_deleted_with_sample_and_speaker(store: SpeakerStore) -> None:
    store.upsert("kasia", "Kasia", None)
    for seed in range(2):
        store.add_sample(
            "kasia", FakeEmbedder().embed(voice(300, seed=seed)), 2.0, "", voice(300, seed=seed)
        )
    folder = store.recordings / "kasia"
    first = store.speakers["kasia"].samples[0].recording
    assert first and (folder / first).is_file()

    assert store.delete_sample("kasia", 0)
    assert not (folder / first).exists()
    assert len(list(folder.glob("*.wav"))) == 1

    assert store.delete("kasia")
    assert not folder.exists()


def test_recording_survives_reload(store: SpeakerStore, tmp_path: Path) -> None:
    store.upsert("kasia", "Kasia", None)
    store.add_sample("kasia", FakeEmbedder().embed(voice(300)), 2.0, "", voice(300))
    again = SpeakerStore(tmp_path / "speakers.json", threshold=0.8, margin=0.05)
    sample = again.speakers["kasia"].samples[0]
    assert sample.recording == store.speakers["kasia"].samples[0].recording
    assert again.speakers["kasia"].summary()["recordings"] == 1


def test_old_store_without_recordings_loads(tmp_path: Path) -> None:
    path = tmp_path / "speakers.json"
    path.write_text(
        json.dumps(
            {"version": 1, "speakers": {"jan": {"name": "Jan", "samples": [
                {"embedding": [1.0, 0.0], "added": "2026-10-01T10:00:00+00:00", "seconds": 2.0}
            ]}}}
        ),
        encoding="utf-8",
    )  # fmt: skip
    store = SpeakerStore(path, threshold=0.8, margin=0.05)
    assert store.speakers["jan"].samples[0].recording == ""
    assert store.speakers["jan"].summary()["recordings"] == 0


def test_rebuild_embeds_recordings_again(store: SpeakerStore) -> None:
    """A "new model" (another embedder) - samples with a recording get new embeddings,
    older samples without a recording keep theirs."""
    embedder = FakeEmbedder()
    store.upsert("kasia", "Kasia", None)
    store.add_sample("kasia", embedder.embed(voice(300, seed=1)), 2.0, "", voice(300, seed=1))
    store.add_sample("kasia", embedder.embed(voice(300, seed=2)), 2.0)  # no recording
    old = store.speakers["kasia"].samples[1].embedding.copy()

    def other_model(speech: np.ndarray) -> np.ndarray:
        vector = np.zeros(len(old), dtype=np.float32)
        vector[0] = 1.0
        return vector

    report = store.rebuild(other_model, max_seconds=8.0)

    assert report["kasia"] == {"rebuilt": 1, "without_recording": 1, "kept": 0}
    samples = store.speakers["kasia"].samples
    assert samples[0].embedding[0] == 1.0
    assert np.array_equal(samples[1].embedding, old)


def test_rebuild_keeps_sample_when_recording_is_missing(store: SpeakerStore) -> None:
    store.upsert("kasia", "Kasia", None)
    store.add_sample("kasia", FakeEmbedder().embed(voice(300)), 2.0, "", voice(300))
    sample = store.speakers["kasia"].samples[0]
    (store.recordings / "kasia" / sample.recording).unlink()

    report = store.rebuild(FakeEmbedder().embed, max_seconds=8.0)

    assert report["kasia"] == {"rebuilt": 0, "without_recording": 0, "kept": 1}
