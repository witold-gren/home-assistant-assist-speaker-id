"""PCM helpers: format conversion, a simple energy VAD and WAV encode/decode."""

from __future__ import annotations

from dataclasses import dataclass
import io
import wave

import numpy as np

RATE = 16000
FRAME_SECONDS = 0.03
# speech frames: louder than the noise floor by this much (and above an absolute floor)
VAD_ABOVE_NOISE_DB = 10.0
VAD_FLOOR_DBFS = -50.0
# keep a little audio around speech so word edges are not cut off
VAD_HANGOVER_FRAMES = 5


@dataclass
class Utterance:
    """Mono float32 audio at RATE and the part of it that is speech."""

    audio: np.ndarray
    speech: np.ndarray

    @property
    def speech_seconds(self) -> float:
        return len(self.speech) / RATE


def pcm_to_float(pcm: bytes, rate: int, width: int, channels: int) -> np.ndarray:
    """Raw PCM (as Wyoming sends it) -> mono float32 at RATE."""
    if width == 2:
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        samples = np.frombuffer(pcm, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        samples = (np.frombuffer(pcm, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported sample width {width}")
    if channels > 1:
        samples = samples[: len(samples) - len(samples) % channels]
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != RATE and len(samples):
        # linear resampling is plenty for speaker embeddings
        duration = len(samples) / rate
        target = np.linspace(0, duration, int(duration * RATE), endpoint=False)
        samples = np.interp(target, np.arange(len(samples)) / rate, samples).astype(np.float32)
    return samples


def speech_only(audio: np.ndarray, max_seconds: float) -> np.ndarray:
    """Concatenated speech frames (energy VAD), at most `max_seconds` of them."""
    frame = int(RATE * FRAME_SECONDS)
    count = len(audio) // frame
    if count == 0:
        return audio[:0]
    frames = audio[: count * frame].reshape(count, frame)
    rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
    dbfs = 20 * np.log10(rms)
    noise = float(np.percentile(dbfs, 10))
    voiced = dbfs > max(noise + VAD_ABOVE_NOISE_DB, VAD_FLOOR_DBFS)
    if VAD_HANGOVER_FRAMES:
        kernel = np.ones(2 * VAD_HANGOVER_FRAMES + 1)
        voiced = np.convolve(voiced.astype(float), kernel, mode="same") > 0
    speech = frames[voiced].reshape(-1)
    return speech[: int(max_seconds * RATE)]


def utterance(pcm: bytes, rate: int, width: int, channels: int, max_seconds: float) -> Utterance:
    audio = pcm_to_float(pcm, rate, width, channels)
    return Utterance(audio, speech_only(audio, max_seconds))


def to_wav(audio: np.ndarray) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(pcm)
    return buffer.getvalue()


def from_wav(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data), "rb") as wav:
        pcm = wav.readframes(wav.getnframes())
        return pcm_to_float(pcm, wav.getframerate(), wav.getsampwidth(), wav.getnchannels())
