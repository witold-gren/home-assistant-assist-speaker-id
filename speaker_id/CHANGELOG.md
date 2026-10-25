# Changelog

## 0.2.0 - 2026-10-25

- Every voice sample keeps its recording (`/data/recordings/<speaker>/*.wav`, also for uploaded
  WAVs and `samples/last`) until the sample, the speaker or all their samples are deleted.
- Enrollment counts all samples of the speaker: `total` / `target` next to `done` / `count` -
  a third series of 10 reports 24 of 30.
- `POST /api/speakers/{id}/reset` - delete all samples, recordings and the own threshold, keep
  the speaker; `POST /api/rebuild` - embed all samples again from their recordings.
- `/api/speakers` reports `recordings`. `speakers.json` of 0.1.0 loads unchanged.

## 0.1.0 - 2026-10-24

First public release of the add-on.

- Wyoming proxy in front of a speech-to-text server (e.g. Whisper): audio unchanged,
  speaker recognition with the 3D-Speaker CAM++ model (sherpa-onnx) at the same time.
- HTTP API: voiceprints, voiceprint enrollment, threshold calibration, settings (threshold,
  margin, minimum speech) saved in `speakers.json`, last utterance (RAM, 10 min).
- Announcing the service to the Assist Speaker ID integration via the Supervisor (with an API token).
