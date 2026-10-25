"""Fixtures: a fake embedder (no model download) and synthetic "voices"."""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pytest

from wyoming_speaker_id.audio import RATE
from wyoming_speaker_id.service import Settings, SpeakerService
from wyoming_speaker_id.speakers import SpeakerStore

DIM = 16


class FakeEmbedder:
    """Embedding = normalised spectrum in DIM bands: tones of different pitch are
    different "speakers", the same pitch with noise is the same speaker."""

    model = "fake.onnx"
    dim = DIM

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, speech: np.ndarray) -> np.ndarray:
        self.calls += 1
        spectrum = np.abs(np.fft.rfft(speech))
        freqs = np.fft.rfftfreq(len(speech), 1 / RATE)
        # 50 Hz bands starting at 50 Hz: 110 and 220 Hz are different voices,
        # 300 and 302 Hz almost the same
        edges = 50 + 50 * np.arange(DIM + 1)
        bands = np.array(
            [spectrum[(freqs >= lo) & (freqs < hi)].sum() for lo, hi in itertools.pairwise(edges)]
        )
        return (bands / (np.linalg.norm(bands) + 1e-9)).astype(np.float32)


def voice(pitch: float, seconds: float = 2.0, seed: int = 0, silence: float = 0.5) -> np.ndarray:
    """A "voice": a tone with harmonics and a little noise, with silence around it."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * RATE)) / RATE
    tone = sum(np.sin(2 * np.pi * pitch * k * t) / k for k in (1, 2, 3))
    speech = 0.3 * tone / 1.8 + 0.01 * rng.standard_normal(len(t))
    pad = np.zeros(int(silence * RATE))
    return np.concatenate([pad, speech, pad]).astype(np.float32)


def pcm(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()


@pytest.fixture
def store(tmp_path: Path) -> SpeakerStore:
    return SpeakerStore(tmp_path / "speakers.json", threshold=0.8, margin=0.05)


@pytest.fixture
def service(store: SpeakerStore) -> SpeakerService:
    return SpeakerService(Settings(upstream="tcp://127.0.0.1:1"), store, FakeEmbedder())
