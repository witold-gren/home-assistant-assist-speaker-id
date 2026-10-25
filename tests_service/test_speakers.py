"""Voiceprints: identification (threshold, margin, unknown), persistence, calibration."""

from __future__ import annotations

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
