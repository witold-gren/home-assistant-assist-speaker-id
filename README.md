# Assist Speaker ID for Home Assistant: who is talking to Assist

**English** · [Polski](README.pl.md)

Assist turns speech into text, but it does not know **who** is speaking. After the question
"What classes do I have today?" there is no way to tell whose timetable to give. Assist Speaker ID
recognises a household member by voice and passes this information on: to a conversation agent,
to an LLM or to automations.

The repository contains two parts:

| Part | Directory | Installation |
|---|---|---|
| The **wyoming-speaker-id** service: a Wyoming proxy in front of a speech-to-text server (e.g. faster-whisper) that recognises the speaker | `speaker_id/` | HA add-on (this repository as an add-on repository) or a Docker container |
| The **Assist Speaker ID** integration: a speech-to-text entity with the speaker, a wrapper agent, a sensor, actions | `custom_components/speaker_id/` | HACS (custom repository, type: integration) |

**Documentation with diagrams** (why and how it works, installation, enrolling voiceprints,
thresholds and calibration, everyday use, privacy):
in English [docs/en](https://witold-gren.github.io/home-assistant-assist-speaker-id/en/),
in Polish [docs/pl](https://witold-gren.github.io/home-assistant-assist-speaker-id/pl/)
(source files: [docs/](docs/)).

## How it works

```
satellite ──audio──▶ HA: STT "Speech to text with speaker" (Assist Speaker ID integration)
                         │ Wyoming
                         ▼
               wyoming-speaker-id ──audio unchanged──▶ Whisper ──▶ text
                 └─ CAM++ voice model ─▶ vector of 192 numbers ─▶ comparison with voiceprints
                         │
                         ▼ transcript {text, speaker, speaker_score, …}
HA: result in the same pipeline run
  ├─ "Agent with speaker" → adds "The person speaking is Anna." and passes it on
  ├─ get_speaker(hass) for other integrations (e.g. your own agent)
  └─ "Last speaker" sensor, speaker_id_identified event
```

- **Recognition:** the model turns the first seconds of speech into a vector. A person's
  voiceprint is the average of the vectors from their recordings. The result is the cosine
  similarity. A person wins only when they exceed their threshold **and** beat the runner-up
  by the margin; otherwise the speaker is "unknown".
- **No extra latency:** the vector is computed while Whisper is transcribing.
  Measured on a Jetson AGX Orin (CPU, 2 threads): median unchanged (1329 → 1330 ms), p90
  +95 ms.
- **The text is always the same as from Whisper alone.** A regular Wyoming client (e.g. the
  built-in Wyoming integration) ignores the extra fields, so the service breaks nothing.
- **The result reaches the conversation agent of the same pipeline run** (`ContextVar`;
  HA runs speech-to-text and intent recognition in a single task, which is checked by the
  test `test_pipeline_passes_speaker_from_stt_to_agent`). Typed text has no speaker.

**Proven in practice:** the author runs the service every day on an **NVIDIA Jetson AGX Orin 64 GB**,
as a container next to faster-whisper, for the whole family and 7 satellites, and it works very well.

### Choice of model (measurements 2026-10-23)

Test sets: 6 Polish Piper voices × 12 sentences and recordings of 3 people from the
sherpa-onnx examples. Accuracy is computed with the "leave one recording out" method; EER is
the point where false acceptances and false rejections are equal.

| Model (sherpa-onnx) | Dimension | 3 s of speech | Piper: accuracy / EER | People: accuracy / EER |
|---|---|---|---|---|
| **3dspeaker CAM++ zh_en "advanced"** (default) | 192 | 91 ms | 100% / 0.2% | 100% / 0% |
| NeMo TitaNet-small | 192 | 59 ms | 100% / 0.9% | 100% / 0% |
| 3dspeaker ERes2Net (VoxCeleb) | 192 | 218 ms | 100% / 1.9% | 100% / 0% |
| WeSpeaker ResNet34-LM | 256 | 257 ms | 98.6% / 12.4% | 92.9% / 15.4% |
| 3dspeaker CAM++ (VoxCeleb) | 512 | 90 ms | 68.1% / 40.8% | 71.4% / 37.7% |

Through the whole chain (Piper → wyoming-speaker-id → Whisper) all 23 utterances
with at least 1 s of speech were attributed to the right person, with scores of 0.76–0.92.
An utterance with 0.6 s of speech gave "unknown", as intended.

## Installation

### Service as an HA add-on

1. Settings → Add-ons → Add-on Store → ⋮ → Repositories: add
   `https://github.com/witold-gren/home-assistant-assist-speaker-id`.
2. Install **Assist Speaker ID**. In the configuration set **Speech-to-text server**:
   `tcp://core-whisper:10300` for the Whisper add-on, or the address of your own server.
3. Start the add-on. It announces itself to Home Assistant (with a random API token), and HA
   offers to set up the integration.

### Service as a container (e.g. next to Whisper on another computer)

```sh
docker build -t wyoming-speaker-id speaker_id/
docker run -d --name speaker-id --restart unless-stopped \
  -p 10350:10350 -p 10351:10351 -v speaker-id-data:/data \
  -e SPEAKER_ID_UPSTREAM=tcp://192.168.1.10:10300 \
  -e SPEAKER_ID_API_TOKEN=long-random-token \
  wyoming-speaker-id
```

| Variable | Default | Meaning |
|---|---|---|
| `SPEAKER_ID_UPSTREAM` | (required) | Wyoming speech-to-text server |
| `SPEAKER_ID_THRESHOLD` / `SPEAKER_ID_MARGIN` | 0.5 / 0.05 | shared threshold and lead over the runner-up |
| `SPEAKER_ID_MIN_SPEECH` / `SPEAKER_ID_MAX_SPEECH` | 1.0 / 8.0 s | below the minimum the speaker is "unknown"; how much speech to use |
| `SPEAKER_ID_MODEL` | CAM++ zh_en | sherpa-onnx model name (downloaded once) or a path |
| `SPEAKER_ID_API_TOKEN` | none | HTTP API token (without it, keep port 10351 inside your home network) |
| `SPEAKER_ID_THREADS`, `SPEAKER_ID_DEBUG` | 2, 0 | model threads, verbose log |

### Service in docker compose (without the add-on)

This is how the author runs it: a container next to Whisper on a Jetson AGX Orin, outside the
HA machine. Audio then goes from HA to the service, and from there to Whisper within one machine.

1. Next to your `docker-compose.yaml`, clone the repository and create an API token:

   ```sh
   git clone https://github.com/witold-gren/home-assistant-assist-speaker-id
   echo "SPEAKER_ID_API_TOKEN=$(openssl rand -hex 24)" >> .env
   ```

2. Add the service to `docker-compose.yaml` (here with an example Whisper; if you already have
   your own, keep it and only change `SPEAKER_ID_UPSTREAM` to its name and port):

   ```yaml
   services:
     whisper:
       image: rhasspy/wyoming-whisper
       command: --model medium-int8 --language pl
       ports: ["10300:10300"]
       volumes: ["./whisper:/data"]
       restart: unless-stopped

     speaker-id:
       build: ./home-assistant-assist-speaker-id/speaker_id
       image: local/wyoming-speaker-id:0.1.0
       container_name: speaker-id
       depends_on: [whisper]
       ports:
         - "10350:10350"          # Wyoming: speech to text with speaker
         - "10351:10351"          # HTTP API: voiceprints, calibration
       volumes:
         - ./speaker-id/data:/data          # speakers.json: vectors only, no recordings
         - /etc/localtime:/etc/localtime:ro
       environment:
         SPEAKER_ID_UPSTREAM: tcp://whisper:10300
         SPEAKER_ID_API_TOKEN: ${SPEAKER_ID_API_TOKEN}
       restart: unless-stopped
   ```

3. Build and start: `docker compose up -d --build speaker-id`. On the first start the service
   downloads the model (about 30 MB) to `/data`. Check: `docker compose logs -f speaker-id`
   and `curl -H "Authorization: Bearer <token>" http://192.168.1.10:10351/api/info`.
4. In HA add the **Assist Speaker ID** integration manually (section [Integration](#integration)):
   host `192.168.1.10`, ports 10350 and 10351, the token from `.env`. The Wyoming integration
   for Whisper alone can stay, but in your voice assistants choose "Speech to text with speaker".
5. Updating: `git -C home-assistant-assist-speaker-id pull && docker compose up -d --build speaker-id`.
   Voiceprints stay in `./speaker-id/data`.

The voice model runs on the CPU, so the container needs neither a GPU nor `runtime: nvidia`,
also on a Jetson (where the GPU stays free for Whisper and Ollama). The full docker compose
stack (Whisper, Piper, Ollama, Assist Speaker ID) is described in the README of
[Assist Router](https://github.com/witold-gren/home-assistant-assist-router#services-in-docker-compose-without-ha-add-ons).

### Integration

Install via HACS (custom repository, type "Integration") and restart HA.
Then: Settings → Devices & services → Add integration → **Assist Speaker ID** (host, Wyoming
port 10350, API port 10351, token). The add-on is configured automatically via discovery.
To change the host, ports or API token later: the integration's ⋮ menu → **Reconfigure**
(people and agent options stay unchanged).

The integration options (Configure) are a menu with two entries:
- **Agent and people:**
  - linking every enrolled voice to an HA person (`person.*`);
  - the agent that "Agent with speaker" passes requests to;
  - the prompt line (by default in the HA language, e.g. `The person speaking is {name}.`);
  - whether to run requests as the speaker's HA user (`context.user_id`).
- **Recognition thresholds:** shared threshold, margin, minimum speech and each person's
  threshold. Saved in the service and effective immediately, without a restart (what the
  numbers mean: [How to read scores and thresholds](#how-to-read-scores-and-thresholds)).

## Usage

1. **Pipeline:** Settings → Voice assistants → pipeline → Speech-to-text:
   **"Speech to text with speaker"**.
2. **Agent:**
   - **"Agent with speaker"** with any target agent. An LLM agent gets the speaker
     information in its system prompt.
   - **Or your own agent that reads the speaker itself:**

     ```python
     try:
         from custom_components.speaker_id.api import get_speaker
     except ImportError:
         get_speaker = None
     speaker = get_speaker(hass) if get_speaker else None
     if speaker and speaker.speaker:
         ...  # speaker.speaker (id), .name, .person, .user_id, .score
     ```

     This is what [Assist Router](https://github.com/witold-gren/home-assistant-assist-router) does:
     a first-person question without a name ("what classes do **I** have") is about the
     speaker's data. On top of that come the speaker's room, family terms ("my brother") and
     the answer to „Kto to mówi?” ("Who is speaking?"). Assist Router currently understands
     Polish. Details: [Assist Router documentation](https://witold-gren.github.io/home-assistant-assist-router/en/).
   - **Enrolling a voiceprint in the agent:** `enrollment_reply(hass)` returns a short reply
     ("Recorded 3 of 10. Next sentence.") and whether to keep listening, or `None` for a
     regular utterance. An agent that calls it first does not execute commands during
     enrollment and does not make the user wait for the model's answer. "Agent with speaker"
     and Assist Router do this themselves.
3. **Automations:** the "Last speaker" sensor (state: name or `unknown`; attributes:
   `speaker`, `name`, `person`, `score`, `scores`, `speech_seconds`, `enrollment`) and the
   `speaker_id_identified` event. The text of the utterance deliberately does not go into the
   history. HA runs automations with Assist sentence triggers before the agent, so they do not
   get the speaker directly. The sensor, however, changes within the same command, just before
   them. A result from a few seconds ago is the speaker of that sentence. An example macro
   (`/config/custom_templates/speaker.jinja`):

   ```jinja
   {% macro speaker(max_s=20) -%}
     {%- set s = states.sensor.assist_speaker_id_last_speaker -%}
     {%- if s and (now() - s.last_updated).total_seconds() <= max_s -%}
       {{- s.attributes.speaker or '' -}}
     {%- endif -%}
   {%- endmacro %}
   ```

   In an automation: `{% from 'speaker.jinja' import speaker %}{% if speaker() == 'anna' %}…`,
   e.g. "where is my phone" then rings the speaker's phone. The sensor's entity id depends on
   the HA language; in English it is `sensor.assist_speaker_id_last_speaker`. After creating
   the file, run the `homeassistant.reload_custom_templates` action.

## Announcements (intercom): your recording instead of synthesis

"OK Nabu, **announcement**, kids, please come to dinner." The other speakers play
**your recording**, exactly what you said after the wake word, without cutting and without
changes, instead of reading the text with a Piper voice.

- The recording comes from the service: it is the last utterance, kept only in RAM.
- HA serves it to the speakers at a one-time, signed address
  `/api/speaker_id/announcement/<token>`, valid for 10 minutes. Nothing is written to
  disk.
- **Action** `speaker_id.announce_last`:

  | Field | Default | Meaning |
  |---|---|---|
  | `satellites` | all satellites that support announcements | where to play |
  | `exclude` | none | satellite to skip, usually the one that recorded |
  | `media_players` | none | additional players, e.g. HomePods via Music Assistant (`play_media` with `announce: true`) |
  | `preannounce` | `true` | short chime before the recording |
  | `prefer_players` | `false` | one speaker per room: an available media player (e.g. a HomePod) instead of the satellite there; `off` counts as unavailable (a disconnected HomePod in Apple TV); the announcing room gets nothing |
  | `max_age` | 60 s | an older last utterance is not announced |

- **For a conversation agent:** `async_announce_last(hass, exclude=…, media_players=…)` from
  `custom_components.speaker_id.api`. Returns a list of entities, or `None` when there is no
  fresh recording (e.g. typed text). Assist Router recognises „ogłoszenie” ("announcement"),
  „komunikat” ("message"), „do wszystkich” ("to everyone"), „intercom” at the start of a
  sentence and calls this function (Assist Router currently understands Polish). The speaker
  that recorded only replies „Ogłoszenie wysłane.” ("Announcement sent.").
- **Important:** in the pipeline turn off "Prefer handling commands locally". Otherwise HA's
  built-in broadcast intent catches "Announcement, …" before the agent and reads the text
  with synthesis. For the same reason the agent would not be able to answer briefly during
  enrollment.

## Voiceprints and calibration

| Action | What it does |
|---|---|
| `speaker_id.start_enrollment` | `speaker: anna`, `name: Anna`, `count: 10`, optionally `satellite: assist_satellite.…`: the next 10 utterances with at least 1.5 s of speech become Anna's samples. **With `satellite` the speaker guides the enrollment itself**, without the wake word. It asks for the first sentence, and after each one says once "Recorded 3 of 10. Next sentence." and keeps listening right away, without executing the command. It asks for a longer one when the sentence was too short, and asks again after silence or a broken-off conversation. It finishes after the last sample ("Done, …") or after 3 attempts without a new one. Without `satellite` every sentence starts with the wake word. Progress is visible in the sensor's `enrollment` attribute. |
| `speaker_id.add_last_sample` | the last utterance (at most 10 min old) as a sample of a person |
| `speaker_id.calibrate` | for each person compares their samples with the samples of the others and suggests a threshold; with `apply: true` saves the thresholds |
| `speaker_id.set_speaker` / `delete_speaker` / `list_speakers` | name and own threshold / delete a voiceprint / list |

**Tips:**
- Record through the satellites and from the distance a household member usually speaks from.
  The microphone and the room change a voice more than the words do.
- Use ordinary commands of a few words; ideally 10–15 per person.
- Calibration shows `separable: false` when the samples of two people overlap. This is common
  with siblings: record more samples or raise `margin`.
- Children's voices change with age, so record new samples every few months.

The service's HTTP API (port 10351, described in `speaker_id/wyoming_speaker_id/http_api.py`)
also supports uploading WAV recordings (`POST /api/speakers/{id}/samples`) and testing
(`POST /api/identify`).

## How to read scores and thresholds

### Similarity: the higher, the more "this is the person"

The model turns a few seconds of speech into a **vector**, i.e. 192 numbers describing the timbre
of the voice: pitch, resonance, manner of articulation. The vector does not depend on the content
of the sentence. A **person's voiceprint** is the average of the vectors from their samples.

Each utterance is compared with every person's voiceprint. The result is the **similarity**
(cosine of the angle between the vectors), usually from 0 to 1:

| Similarity | What it usually means (CAM++ model, satellite microphone) |
|---|---|
| 0.75–0.95 | the same person, good conditions |
| 0.55–0.75 | the same person, but briefly, from far away, in noise or in a different tone |
| 0.35–0.55 | a similar voice, often siblings or a parent and child of the same sex |
| 0–0.35 | a different person |

This **is not the "uniqueness" of a voice**. A high score means that *this utterance* is very
similar to *this voiceprint*. „Kto to mówi?” ("Who is speaking?") gives this number as a percentage.

### Threshold and margin: the bar to clear

The best-matching person is recognised only when both conditions are met:

1. **similarity ≥ that person's threshold**, i.e. their own threshold, or the shared one when
   they have none;
2. **similarity − runner-up's similarity ≥ margin.** With two almost equal scores the answer
   is "I'm not sure" rather than a guess.

If either condition is not met, the speaker is "unknown" and Assist works as it would without
recognition.

- **A higher threshold** is stricter: fewer mistakes, but more often "unknown".
- **A lower threshold** is more lenient: less often "unknown", but mistakes are easier.

Similar voices need a **higher** threshold or a larger margin. Not because their voice is "less
unique" in the sense of a weaker voiceprint, but because other voices come close to their
voiceprint. So the bar has to be higher, so that a sibling does not clear it.

| Parameter | Default | Where |
|---|---|---|
| Shared threshold | 0.50 | integration options → Recognition thresholds (or `SPEAKER_ID_THRESHOLD` / the add-on option as a start value) |
| Person's threshold | none = shared | integration options → Recognition thresholds, the `speaker_id.set_speaker` action, calibration with `apply: true` |
| Margin | 0.05 | integration options (or `SPEAKER_ID_MARGIN`) |
| Minimum speech | 1.0 s | integration options (or `SPEAKER_ID_MIN_SPEECH`); a shorter utterance = always "unknown" |

Values set in the integration options are saved in the service's `speakers.json` and take
precedence over environment variables and add-on options. Those are only a start value
on the first start. A change takes effect from the next utterance, without a restart.

### Calibration report

`speaker_id.calibrate` computes for each person:

| Field | Meaning |
|---|---|
| `samples` | number of samples |
| `own_min` | the **worst** own sample: similarity to the voiceprint built from the *other* samples of that person (a sample is not compared with itself) |
| `own_p10` | 10th percentile of own samples: 9 out of 10 samples are at least this similar |
| `own_mean` | average similarity of own samples |
| `others_max` | the **most similar foreign** sample, i.e. the highest similarity of another person's sample to this voiceprint |
| `separable` | `true` when `own_min > others_max`: no foreign sample came close to the worst own one |
| `suggested_threshold` | midpoint between `own_p10` and `others_max` (within 0.2–0.9) |

**Example (Tomek):**

```yaml
samples: 10
own_min: 0.709      # the worst own sample is 71% similar to the voiceprint
own_p10: 0.778      # 9 out of 10 own samples have at least 78%
own_mean: 0.803     # 80% on average
others_max: 0.305   # the most similar foreign sample has only 31%
separable: true     # 0.709 > 0.305 - voices clearly separated
suggested_threshold: 0.541   # (0.778 + 0.305) / 2
```

Between the worst own sample (71%) and the closest foreign one (31%) there is a **40-point gap**.
The voice is very easy to tell apart. Any threshold in the ~0.35–0.70 range works;
the suggested 0.54 lies in the middle, with headroom on both sides. Your observations (above ~60%
it is definitely this person) agree with this: 0.60 is also safe.

For a child, calibration may show for example `own_p10: 0.74`, `others_max: 0.68`
(siblings). Then `suggested_threshold: 0.71`, and the gap is only 6 points. The threshold has
to be high, and a margin is still worth having.

### How to calibrate similar voices (e.g. siblings)

1. **More and better samples:** 15–20 per person, recorded through the satellite the person
   usually speaks to, from the usual distance, in a normal tone. Shouting, whispering and
   laughing lower the score.
2. **Calibrate after everyone is enrolled:** `others_max` is computed only from people who
   already have voiceprints.
3. **Check `separable`:** with `false` some foreign sample exceeded the worst own one.
   Often a single failed sample (someone interrupted, there was noise) lowers `own_min`.
   Record more samples or enrol that person again (`delete_speaker` and enrollment).
4. **Threshold between `others_max` and `own_p10`:** closer to `own_p10` means fewer mistakes,
   but more often "unknown". Closer to `others_max` works the other way round.
5. **Margin 0.08–0.15 for siblings:** instead of guessing when two scores are close,
   the assistant says "I'm not sure".
6. **The „Kto to mówi?” ("Who is speaking?") test** on a few longer sentences from each person.
   Look at the similarity and at the "next person" score: the gap between them is your actual
   headroom.

Calibration with `apply: true` overwrites the per-person thresholds. Make manual adjustments
after it, or do not use `apply`.

## Limitations

- **Short commands:** "Yes", "Stop" and similar (under 1 s of speech) give "unknown".
- **The wake word is not used:** the satellite detects it itself, so only the command reaches
  speech-to-text. Recognition does not depend on the content.
- **This is convenience, not security:** a voice can be recorded and played back. Do not use
  the speaker to open locks or disarm an alarm.

## Privacy

A voiceprint is biometric data (GDPR Article 9):
- everything runs locally;
- the service stores only vectors (`/data/speakers.json`), no recordings;
- the last utterance is kept only in memory, for at most 10 minutes (for
  `add_last_sample`);
- household members should know about the recognition and agree to it;
- `delete_speaker` deletes a person's voiceprint.

## Development

```sh
python3.13 -m venv .venv-service && .venv-service/bin/pip install -r speaker_id/requirements.txt pytest pytest-asyncio ruff
python3.13 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
scripts/test.sh     # service and integration tests, ruff
./deploy.sh         # integration to $CONFIG/custom_components (a mounted Samba share)
```

License: Apache-2.0. 3D-Speaker CAM++ model: Apache-2.0 (modelscope/3D-Speaker), via
[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx).
