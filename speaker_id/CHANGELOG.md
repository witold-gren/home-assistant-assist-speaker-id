# Changelog

## 0.1.0 - 2026-10-24

First public release of the add-on.

- Wyoming proxy in front of a speech-to-text server (e.g. Whisper): audio unchanged,
  speaker recognition with the 3D-Speaker CAM++ model (sherpa-onnx) at the same time.
- HTTP API: voiceprints, voiceprint enrollment, threshold calibration, settings (threshold,
  margin, minimum speech) saved in `speakers.json`, last utterance (RAM, 10 min).
- Announcing the service to the Assist Speaker ID integration via the Supervisor (with an API token).
