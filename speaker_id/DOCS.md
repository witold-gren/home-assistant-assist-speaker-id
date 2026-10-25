# Assist Speaker ID - speaker recognition

The add-on sits between Home Assistant and your speech-to-text server (e.g. the **Whisper**
add-on). It forwards the audio unchanged, and meanwhile computes a "voice fingerprint"
and compares it with the stored voiceprints of the household members. The text returns to HA
as usual, together with the information about who was speaking.

1. Set **Speech-to-text server** (for the Whisper add-on: `tcp://core-whisper:10300`)
   and start the add-on.
2. HA offers the **Assist Speaker ID** integration (Settings → Devices & services). Add it.
3. In your Assist pipelines set speech-to-text to **"Speech to text with speaker"**.
4. Enrol the voice of every household member: the `speaker_id.start_enrollment` action (e.g.
   `speaker: anna`, `count: 10`, `satellite: assist_satellite.living_room_…`). The speaker asks
   for each sentence itself, and that person answers with 10 ordinary commands. Without
   `satellite` every command has to start with the wake word.
5. When everyone has a voiceprint: the `speaker_id.calibrate` action with `apply: true`.

Thresholds, margin and minimum speech are set in the integration options (Recognition
thresholds). The add-on options are only a start value. What the numbers mean and how to
calibrate similar voices (e.g. siblings): the repository README, section "How to read scores
and thresholds".

**Announcements (intercom):** the `speaker_id.announce_last` action plays the last utterance,
i.e. the speaker's recording unchanged, on the other speakers. Details: README, section
"Announcements".

**Privacy:** a voiceprint is biometric data. The add-on stores the vectors in
`/data/speakers.json` and the recording of every voice sample in `/data/recordings/<person>/`
(about 0.2 MB per sample, needed to rebuild voiceprints with `speaker_id.rebuild_voiceprints`),
and sends nothing outside your network. `speaker_id.reset_speaker` deletes a person's samples and
recordings, `speaker_id.delete_speaker` the whole person.

Full documentation: [English](https://witold-gren.github.io/home-assistant-assist-speaker-id/en/).
Dokumentacja po polsku (Polish documentation): [witold-gren.github.io/home-assistant-assist-speaker-id/pl/](https://witold-gren.github.io/home-assistant-assist-speaker-id/pl/).
