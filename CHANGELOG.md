# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions: [SemVer](https://semver.org/).
Add-on (service) changes: [speaker_id/CHANGELOG.md](speaker_id/CHANGELOG.md).

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
