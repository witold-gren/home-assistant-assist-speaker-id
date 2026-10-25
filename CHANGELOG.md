# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions: [SemVer](https://semver.org/).
Add-on (service) changes: [speaker_id/CHANGELOG.md](speaker_id/CHANGELOG.md).

## [0.2.0] - 2026-10-25

### Added
- Probable speaker: an unrecognized voice (below its person's threshold or too close to the
  runner-up) whose best similarity reaches the new option **Probable speaker from** (default
  0.40) is reported as `probable`, `probable_name`, `probable_person`, `probable_user_id` -
  sensor attributes, the `speaker_id_identified` event and `get_speaker()`. "Agent with
  speaker" adds a "probably {name}" prompt line; nothing runs as that person's user.
- Sensor attribute `user_id` (the recognized person's HA user).
- Adding samples in series: another enrollment adds to the saved samples and the satellite counts
  all of them („Dogrywam próbki głosu: Anna, zapisane są 20, nagrywam do 30”, „Nagrano 24 z 30”,
  „Gotowe. Wzorzec głosu: Anna, liczba próbek: 30.”). Needs the service 0.2.0, which keeps the
  recording of every sample; an older service counts the series only.
- Actions `speaker_id.reset_speaker` (delete the samples and recordings, keep the person) and
  `speaker_id.rebuild_voiceprints` (embed all samples again from their recordings). The thresholds
  options show samples and recordings per person; `delete_speaker` deletes the recordings too.

### Changed
- Entity ids no longer depend on the HA language: `sensor.speaker_id_last_speaker`,
  `stt.speaker_id_speech_to_text`, `conversation.speaker_id_agent`. Default ids of 0.1.x
  (e.g. `sensor.speaker_id_ostatni_mowca`) are renamed on setup; ids changed by hand stay.
  Pipelines that use the old speech-to-text id must be switched to the new one.

## [0.1.1] - 2026-10-25

### Added
- Logo images (`logo`, `dark_logo`) next to the icons in `brand/`.

## [0.1.0] - 2026-10-25

First public release of the Assist Speaker ID integration (domain `speaker_id`).

### Added
- `prefer_players` for announcements (`speaker_id.announce_last`, `async_announce_last()`):
  one speaker per room - an available media player (e.g. a HomePod) plays instead of the
  satellite in that room; a player in state `off` (a disconnected HomePod in Apple TV) counts as
  unavailable and the satellite plays; the room the announcement comes from gets nothing.
- Speech to text with the speaker ("Speech to text with speaker"); the result is available to
  the conversation agent of the same pipeline run (`get_speaker()`), the "Last speaker" sensor,
  the `speaker_id_identified` event.
- "Agent with speaker": passes requests to the chosen agent with a line about the speaker in
  the prompt.
- Guided voiceprint enrollment on a satellite (without the wake word), short agent replies
  during enrollment (`enrollment_reply()`), threshold calibration.
- Options: agent and people (linking voices to HA persons), recognition thresholds.
- "Reconfigure": change the host, ports and API token without losing the options.
- Announcements (intercom): `speaker_id.announce_last` - the speaker's recording on other speakers.
