"""Shared service state: settings, voiceprints, the embedder, last utterance, enrollment."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
import time
from typing import Any

import numpy as np

from .audio import Utterance, to_wav
from .embedder import Embedder
from .speakers import Identification, SpeakerStore

_LOGGER = logging.getLogger(__name__)

# the last utterance stays in RAM only this long (enrollment from "what I just said")
LAST_SECONDS = 600


@dataclass
class Settings:
    upstream: str
    min_speech: float = 1.0  # shorter speech -> always unknown (startup value, see store)
    max_speech: float = 8.0  # only the first seconds of speech are embedded
    enroll_min_speech: float = 1.5  # shorter utterances are not taken as samples
    info_ttl: float = 60.0


@dataclass
class Enrollment:
    speaker: str
    count: int
    done: int = 0
    scores: list[float | None] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"speaker": self.speaker, "count": self.count, "done": self.done}


@dataclass
class LastUtterance:
    at: float
    text: str
    speech_seconds: float
    result: Identification | None
    embedding: np.ndarray | None
    audio: np.ndarray

    def to_dict(self) -> dict[str, Any]:
        return {
            "age_seconds": round(time.monotonic() - self.at, 1),
            "text": self.text,
            "speech_seconds": round(self.speech_seconds, 2),
            **(self.result.to_dict() if self.result else {"speaker": None}),
        }


class SpeakerService:
    def __init__(self, settings: Settings, store: SpeakerStore, embedder: Embedder) -> None:
        self.settings = settings
        self.store = store
        self.embedder = embedder
        self.enrollment: Enrollment | None = None
        self._last: LastUtterance | None = None
        self._info: tuple[float, Any] | None = None
        self.info_lock = asyncio.Lock()

    # --- identification of one utterance (runs in an executor thread) ---

    def analyse(self, utt: Utterance) -> tuple[np.ndarray | None, Identification]:
        if utt.speech_seconds < self.store.min_speech:
            return None, Identification(
                None, None, 0.0, {}, f"too short ({utt.speech_seconds:.1f} s of speech)"
            )
        embedding = self.embedder.embed(utt.speech)
        return embedding, self.store.identify(embedding)

    def finish(
        self, utt: Utterance, embedding: np.ndarray | None, result: Identification, text: str
    ) -> dict[str, Any]:
        """Remember the utterance, feed an active enrollment; extra transcript fields."""
        self._last = LastUtterance(
            time.monotonic(), text, utt.speech_seconds, result, embedding, utt.audio
        )
        extra: dict[str, Any] = {**result.to_dict(), "speech_seconds": round(utt.speech_seconds, 2)}
        enrollment = self.enrollment
        if enrollment is not None:
            if embedding is not None and utt.speech_seconds >= self.settings.enroll_min_speech:
                score = self.store.add_sample(
                    enrollment.speaker, embedding, utt.speech_seconds, text
                )
                enrollment.done += 1
                enrollment.scores.append(score)
                _LOGGER.info(
                    "Enrollment %s: sample %d/%d (score vs voiceprint %s)",
                    enrollment.speaker,
                    enrollment.done,
                    enrollment.count,
                    "-" if score is None else f"{score:.2f}",
                )
                if enrollment.done >= enrollment.count:
                    self.enrollment = None
            extra["enrollment"] = enrollment.to_dict()
        _LOGGER.info(
            "Speaker %s (score %.2f, %.1f s speech: %s): %s",
            result.speaker or "unknown",
            result.score,
            utt.speech_seconds,
            result.reason,
            text,
        )
        return extra

    # --- last utterance ---

    def last(self) -> LastUtterance | None:
        if self._last and time.monotonic() - self._last.at <= LAST_SECONDS:
            return self._last
        self._last = None
        return None

    def last_wav(self) -> bytes | None:
        last = self.last()
        return to_wav(last.audio) if last else None

    def add_last_as_sample(self, speaker_id: str) -> float | None:
        last = self.last()
        if last is None or last.embedding is None:
            raise LookupError("no recent utterance with enough speech")
        return self.store.add_sample(speaker_id, last.embedding, last.speech_seconds, last.text)

    # --- upstream info cache ---

    def cached_info(self) -> Any | None:
        if self._info and time.monotonic() - self._info[0] < self.settings.info_ttl:
            return self._info[1]
        return None

    def cache_info(self, info: Any) -> None:
        self._info = (time.monotonic(), info)
