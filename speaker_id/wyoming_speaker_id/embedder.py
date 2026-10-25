"""Speaker embeddings with sherpa-onnx (CAM++ by default, any sherpa-onnx speaker model)."""

from __future__ import annotations

import logging
from pathlib import Path
import threading
import time
import urllib.request

import numpy as np

from .audio import RATE

_LOGGER = logging.getLogger(__name__)

MODELS_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
# 3D-Speaker CAM++ zh+en "advanced": in a test on 2026-10-23 (Piper voices and human recordings)
# EER ~0%, 3 s of speech in ~90 ms on a Jetson; CAM++ VoxCeleb (512 dimensions) had EER ~40%.
DEFAULT_MODEL = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"


def ensure_model(name_or_path: str, models_dir: Path) -> Path:
    """Path of the model file; a bare sherpa-onnx model name is downloaded once."""
    path = Path(name_or_path)
    if path.is_file():
        return path
    target = models_dir / path.name
    if not target.is_file():
        models_dir.mkdir(parents=True, exist_ok=True)
        _LOGGER.info("Downloading speaker model %s", path.name)
        tmp = target.with_suffix(".part")
        urllib.request.urlretrieve(MODELS_URL + path.name, tmp)
        tmp.rename(target)
    return target


class Embedder:
    """Thread-safe wrapper: sherpa-onnx extractor objects are not re-entrant."""

    def __init__(self, model: Path, threads: int) -> None:
        import sherpa_onnx  # heavy import, only where it is used

        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model), num_threads=threads)
        if not config.validate():
            raise ValueError(f"invalid speaker model {model}")
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        self._lock = threading.Lock()
        self.model = model.name
        self.dim = self._extractor.dim

    def embed(self, speech: np.ndarray) -> np.ndarray:
        started = time.perf_counter()
        with self._lock:
            stream = self._extractor.create_stream()
            stream.accept_waveform(RATE, speech.astype(np.float32))
            stream.input_finished()
            vector = np.asarray(self._extractor.compute(stream), dtype=np.float32)
        _LOGGER.debug(
            "Embedding of %.1f s in %.0f ms",
            len(speech) / RATE,
            (time.perf_counter() - started) * 1000,
        )
        return vector
