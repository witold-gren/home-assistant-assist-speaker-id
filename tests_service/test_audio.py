"""PCM conversion, energy VAD, WAV."""

from __future__ import annotations

import numpy as np

from wyoming_speaker_id.audio import RATE, from_wav, pcm_to_float, speech_only, to_wav, utterance

from .conftest import pcm, voice


def test_vad_drops_silence_and_caps_length() -> None:
    audio = voice(150, seconds=2.0, silence=1.0)  # 1 s ciszy + 2 s mowy + 1 s ciszy

    speech = speech_only(audio, max_seconds=8)
    capped = speech_only(audio, max_seconds=1)

    assert 1.9 <= len(speech) / RATE <= 2.4  # speech + a short margin at the edges
    assert len(capped) / RATE == 1.0


def test_silence_only_has_no_speech() -> None:
    assert len(speech_only(np.zeros(RATE * 2, dtype=np.float32), 8)) / RATE < 0.5


def test_stereo_44k_is_mono_16k() -> None:
    mono = voice(150, seconds=1.0, silence=0)
    t = np.arange(int(len(mono) / RATE * 44100))
    left = np.interp(t / 44100, np.arange(len(mono)) / RATE, mono)
    stereo = np.stack([left, left], axis=1).reshape(-1)
    raw = (stereo * 32767).astype("<i2").tobytes()

    out = pcm_to_float(raw, 44100, 2, 2)

    assert abs(len(out) - len(mono)) <= 2


def test_utterance_from_pcm() -> None:
    utt = utterance(pcm(voice(150, seconds=1.5)), RATE, 2, 1, 8.0)
    assert 1.4 <= utt.speech_seconds <= 1.9


def test_wav_roundtrip() -> None:
    audio = voice(150, seconds=0.5, silence=0)
    back = from_wav(to_wav(audio))
    assert len(back) == len(audio)
    assert np.allclose(back, audio, atol=1e-3)
