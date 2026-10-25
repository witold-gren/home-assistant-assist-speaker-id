"""Voiceprints: enrolled speakers, identification and threshold calibration.

A voiceprint is the mean of the speaker's L2-normalised sample embeddings,
normalised again. Identification is open-set: the best speaker wins only when
its cosine score reaches the threshold AND beats the runner-up by a margin;
otherwise the answer is "unknown" (None).

Only embeddings are stored (no audio) - a voiceprint is biometric data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any

import numpy as np

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
SPEAKER_ID = re.compile(r"^[a-z0-9_]{1,32}$")
# calibration needs a few samples per speaker to say anything useful
MIN_CALIBRATION_SAMPLES = 3


def normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


@dataclass
class Sample:
    embedding: np.ndarray
    added: str
    seconds: float = 0.0
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "embedding": [round(float(x), 6) for x in self.embedding],
            "added": self.added,
            "seconds": round(self.seconds, 2),
            "text": self.text,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Sample:
        return Sample(
            embedding=normalize(np.asarray(data["embedding"], dtype=np.float32)),
            added=data.get("added", ""),
            seconds=float(data.get("seconds", 0.0)),
            text=data.get("text", ""),
        )


@dataclass
class Speaker:
    id: str
    name: str
    threshold: float | None = None  # None = the global threshold
    samples: list[Sample] = field(default_factory=list)

    def voiceprint(self, skip: int | None = None) -> np.ndarray | None:
        vectors = [s.embedding for i, s in enumerate(self.samples) if i != skip]
        if not vectors:
            return None
        return normalize(np.mean(vectors, axis=0))

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "threshold": self.threshold,
            "samples": len(self.samples),
            "seconds": round(sum(s.seconds for s in self.samples), 1),
        }


@dataclass
class Identification:
    speaker: str | None  # None = unknown
    name: str | None
    score: float  # cosine score of the best speaker (also when rejected)
    scores: dict[str, float]
    reason: str  # why accepted or rejected - for logs and calibration

    def to_dict(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker,
            "speaker_name": self.name,
            "speaker_score": round(self.score, 3),
            "speaker_scores": {k: round(v, 3) for k, v in self.scores.items()},
            "speaker_reason": self.reason,
        }


class SpeakerStore:
    """Enrolled speakers persisted in a JSON file (atomic writes, thread-safe)."""

    def __init__(
        self, path: Path, threshold: float, margin: float, min_speech: float = 1.0
    ) -> None:
        self.path = path
        # start values (add-on options / environment variables); values set later through
        # the API (integration options in HA) are saved in the file and take precedence
        self.threshold = threshold
        self.margin = margin
        self.min_speech = min_speech
        self.settings_from = "startup"
        self.speakers: dict[str, Speaker] = {}
        self._lock = threading.RLock()
        self.load()

    # --- persistence ---

    def load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        with self._lock:
            self.speakers = {
                sid: Speaker(
                    id=sid,
                    name=s.get("name", sid),
                    threshold=s.get("threshold"),
                    samples=[Sample.from_dict(x) for x in s.get("samples", [])],
                )
                for sid, s in data.get("speakers", {}).items()
            }
            if settings := data.get("settings"):
                self.threshold = float(settings.get("threshold", self.threshold))
                self.margin = float(settings.get("margin", self.margin))
                self.min_speech = float(settings.get("min_speech", self.min_speech))
                self.settings_from = "file"
        _LOGGER.info(
            "Loaded %d speaker(s) from %s; threshold %.2f, margin %.2f, min speech %.1f s (%s)",
            len(self.speakers),
            self.path,
            self.threshold,
            self.margin,
            self.min_speech,
            self.settings_from,
        )

    def save(self) -> None:
        with self._lock:
            data: dict[str, Any] = {
                "version": STORE_VERSION,
                "speakers": {
                    sid: {
                        "name": s.name,
                        "threshold": s.threshold,
                        "samples": [x.to_dict() for x in s.samples],
                    }
                    for sid, s in self.speakers.items()
                },
            }
            if self.settings_from == "file":
                data["settings"] = self.settings()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".speakers-")
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False)
            os.replace(tmp, self.path)

    # --- settings ---

    def settings(self) -> dict[str, float]:
        return {"threshold": self.threshold, "margin": self.margin, "min_speech": self.min_speech}

    def set_settings(
        self,
        threshold: float | None = None,
        margin: float | None = None,
        min_speech: float | None = None,
    ) -> dict[str, float]:
        """Shared threshold, margin, minimum speech - saved in the file (survive a restart)."""
        if threshold is not None and not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold: 0-1")
        if margin is not None and not 0.0 <= margin <= 0.5:
            raise ValueError("margin: 0-0.5")
        if min_speech is not None and not 0.3 <= min_speech <= 5.0:
            raise ValueError("min_speech: 0.3-5 s")
        with self._lock:
            if threshold is not None:
                self.threshold = threshold
            if margin is not None:
                self.margin = margin
            if min_speech is not None:
                self.min_speech = min_speech
            self.settings_from = "file"
            self.save()
            return self.settings()

    # --- management ---

    def upsert(self, speaker_id: str, name: str | None, threshold: float | None) -> Speaker:
        if not SPEAKER_ID.match(speaker_id):
            raise ValueError("speaker id: 1-32 characters a-z, 0-9, _")
        if threshold is not None and not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold: 0-1")
        with self._lock:
            speaker = self.speakers.get(speaker_id)
            if speaker is None:
                speaker = self.speakers[speaker_id] = Speaker(speaker_id, name or speaker_id)
            if name:
                speaker.name = name
            speaker.threshold = threshold
            self.save()
            return speaker

    def delete(self, speaker_id: str) -> bool:
        with self._lock:
            if self.speakers.pop(speaker_id, None) is None:
                return False
            self.save()
            return True

    def add_sample(
        self, speaker_id: str, embedding: np.ndarray, seconds: float, text: str = ""
    ) -> float | None:
        """Add a sample; returns its score against the voiceprint before adding (None for
        the first sample) - a low score means a wrong person or a bad recording."""
        with self._lock:
            speaker = self.speakers[speaker_id]
            before = speaker.voiceprint()
            vector = normalize(np.asarray(embedding, dtype=np.float32))
            speaker.samples.append(
                Sample(vector, datetime.now(UTC).isoformat(timespec="seconds"), seconds, text)
            )
            self.save()
            return None if before is None else float(np.dot(vector, before))

    def delete_sample(self, speaker_id: str, index: int) -> bool:
        with self._lock:
            samples = self.speakers[speaker_id].samples
            if not 0 <= index < len(samples):
                return False
            del samples[index]
            self.save()
            return True

    # --- identification ---

    def identify(self, embedding: np.ndarray) -> Identification:
        vector = normalize(np.asarray(embedding, dtype=np.float32))
        with self._lock:
            prints = {
                sid: (s, vp)
                for sid, s in self.speakers.items()
                if (vp := s.voiceprint()) is not None
            }
        scores = {sid: float(np.dot(vector, vp)) for sid, (_, vp) in prints.items()}
        if not scores:
            return Identification(None, None, 0.0, {}, "no enrolled speakers")
        ranked = sorted(scores.items(), key=lambda item: -item[1])
        best_id, best = ranked[0]
        speaker = prints[best_id][0]
        threshold = speaker.threshold if speaker.threshold is not None else self.threshold
        if best < threshold:
            return Identification(
                None, None, best, scores, f"{best_id} below threshold {threshold:.2f}"
            )
        if len(ranked) > 1 and best - ranked[1][1] < self.margin:
            return Identification(
                None, None, best, scores, f"{best_id} too close to {ranked[1][0]}"
            )
        return Identification(best_id, speaker.name, best, scores, "match")

    # --- calibration ---

    def calibrate(self) -> dict[str, Any]:
        """Per speaker: leave-one-out scores of own samples vs scores of everybody
        else's samples against this voiceprint; suggested threshold in between.

        `separable` False means some impostor sample scores at least as high as
        some own sample - such speakers need more (or cleaner) samples.
        """
        with self._lock:
            speakers = list(self.speakers.values())
        report: dict[str, Any] = {"speakers": {}, "margin": self.margin}
        for speaker in speakers:
            if len(speaker.samples) < MIN_CALIBRATION_SAMPLES:
                report["speakers"][speaker.id] = {
                    "samples": len(speaker.samples),
                    "note": f"needs at least {MIN_CALIBRATION_SAMPLES} samples",
                }
                continue
            own = [
                float(np.dot(sample.embedding, speaker.voiceprint(skip=i)))
                for i, sample in enumerate(speaker.samples)
            ]
            voiceprint = speaker.voiceprint()
            others = [
                float(np.dot(sample.embedding, voiceprint))
                for other in speakers
                if other.id != speaker.id
                for sample in other.samples
            ]
            low_own = float(np.percentile(own, 10))
            high_other = max(others) if others else 0.0
            suggested = (low_own + high_other) / 2 if others else low_own - 0.1
            report["speakers"][speaker.id] = {
                "samples": len(speaker.samples),
                "own_min": round(min(own), 3),
                "own_p10": round(low_own, 3),
                "own_mean": round(float(np.mean(own)), 3),
                "others_max": round(high_other, 3),
                "separable": not others or min(own) > high_other,
                "suggested_threshold": round(float(np.clip(suggested, 0.2, 0.9)), 3),
            }
        return report

    def apply_calibration(self, report: dict[str, Any]) -> None:
        with self._lock:
            for sid, entry in report["speakers"].items():
                if "suggested_threshold" in entry and sid in self.speakers:
                    self.speakers[sid].threshold = entry["suggested_threshold"]
            self.save()
