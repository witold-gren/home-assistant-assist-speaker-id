"""The speaker of the utterance being processed.

The STT entity sets `CURRENT` while Home Assistant's assist pipeline runs speech to
text. Intent recognition (the conversation agent) runs later in the same task, so it
sees the same value - and a typed text conversation (no STT) sees None.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import time
from typing import Any


@dataclass(frozen=True)
class SpeakerResult:
    speaker: str | None  # service speaker id, None = unknown
    name: str | None
    score: float
    scores: dict[str, float] = field(default_factory=dict)
    speech_seconds: float = 0.0
    reason: str = ""
    person: str | None = None  # person entity id mapped in the options
    user_id: str | None = None  # that person's Home Assistant user
    # unrecognized voice: the most similar speaker when close enough (probable_threshold)
    probable: str | None = None
    probable_name: str | None = None
    probable_person: str | None = None
    probable_user_id: str | None = None
    enrollment: dict[str, Any] | None = None
    # utterance during enrollment: True = saved as a sample, False = too short;
    # None = a normal utterance
    sample_added: bool | None = None
    at: float = field(default_factory=time.monotonic)

    @staticmethod
    def from_transcript(data: dict[str, Any]) -> SpeakerResult | None:
        """From the extra fields of a wyoming-speaker-id `transcript` event."""
        if "speaker_score" not in data and "speaker" not in data:
            return None  # a plain ASR server, not wyoming-speaker-id
        return SpeakerResult(
            speaker=data.get("speaker"),
            name=data.get("speaker_name"),
            score=float(data.get("speaker_score") or 0.0),
            scores=dict(data.get("speaker_scores") or {}),
            speech_seconds=float(data.get("speech_seconds") or 0.0),
            reason=data.get("speaker_reason", ""),
            enrollment=data.get("enrollment"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker,
            "name": self.name,
            "person": self.person,
            "score": self.score,
            "scores": self.scores,
            "speech_seconds": self.speech_seconds,
            "reason": self.reason,
            "probable": self.probable,
            "probable_name": self.probable_name,
            "probable_person": self.probable_person,
            "probable_user_id": self.probable_user_id,
            "user_id": self.user_id,
            "enrollment": self.enrollment,
        }


CURRENT: ContextVar[SpeakerResult | None] = ContextVar("speaker_id_current", default=None)
