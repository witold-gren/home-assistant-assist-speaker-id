"""Assist Speaker ID integration against a real wyoming-speaker-id on localhost."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable

from homeassistant.components import conversation, stt
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import intent
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.speaker_id.api import get_speaker
from custom_components.speaker_id.const import (
    CONF_AGENT,
    CONF_API_PORT,
    CONF_API_TOKEN,
    CONF_PEOPLE,
    CONF_PROMPT,
    CONF_SET_USER,
    DOMAIN,
    EVENT_IDENTIFIED,
)
from custom_components.speaker_id.speaker import CURRENT

from .conftest import Stack, pcm, voice

STT = "stt.assist_speaker_id_speech_to_text_with_speaker"
AGENT = "conversation.assist_speaker_id_agent_with_speaker"
SENSOR = "sensor.assist_speaker_id_last_speaker"
METADATA = stt.SpeechMetadata(
    language="pl",
    format=stt.AudioFormats.WAV,
    codec=stt.AudioCodecs.PCM,
    bit_rate=stt.AudioBitRates.BITRATE_16,
    sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
    channel=stt.AudioChannels.CHANNEL_MONO,
)


class RecordingAgent(conversation.AbstractConversationAgent):
    """Target agent: remembers what it got."""

    def __init__(self) -> None:
        self.inputs: list[conversation.ConversationInput] = []

    @property
    def supported_languages(self) -> list[str]:
        return ["pl"]

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        self.inputs.append(user_input)
        response = intent.IntentResponse(language="pl")
        response.async_set_speech("ok")
        return conversation.ConversationResult(response=response)


async def _stream(audio: bytes) -> AsyncIterable[bytes]:
    for i in range(0, len(audio), 1024):
        yield audio[i : i + 1024]


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, **options) -> None:
    entry.add_to_hass(hass)
    if options:
        hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _transcribe(hass: HomeAssistant, audio: bytes) -> stt.SpeechResult:
    engine = stt.async_get_speech_to_text_engine(hass, STT)
    assert engine is not None
    return await engine.async_process_audio_stream(METADATA, _stream(audio))


# --- config flow ---


async def test_user_flow(hass: HomeAssistant, stack: Stack) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"host": "127.0.0.1", "port": stack.port, CONF_API_PORT: stack.api_port},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["port"] == stack.port


async def test_user_flow_cannot_connect(hass: HomeAssistant, stack: Stack) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": 1, CONF_API_PORT: stack.api_port}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_sets_token_and_keeps_options(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry, **{CONF_PROMPT: "Pyta: {name}."})
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    data = {"host": "127.0.0.1", "port": stack.port, CONF_API_PORT: stack.api_port}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**data, CONF_API_TOKEN: "secret"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_API_TOKEN] == "secret"
    assert entry.options[CONF_PROMPT] == "Pyta: {name}."

    # puste pole usuwa token
    result = await entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(result["flow_id"], data)
    assert entry.data[CONF_API_TOKEN] is None


# --- speech to text ---


async def test_stt_text_and_speaker(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    stack.enroll("kasia", "Kasia", 300)
    hass.states.async_set("person.kasia", "home", {"user_id": "user-kasia"})
    await _setup(hass, entry, **{CONF_PEOPLE: {"kasia": "person.kasia"}})
    events = []
    hass.bus.async_listen(EVENT_IDENTIFIED, events.append)

    result = await _transcribe(hass, pcm(voice(300, seed=5)))
    await hass.async_block_till_done()

    assert result.result is stt.SpeechResultState.SUCCESS
    assert result.text == "jakie mam dzisiaj zajęcia"
    speaker = get_speaker(hass)
    assert speaker is not None
    assert (speaker.speaker, speaker.name, speaker.person, speaker.user_id) == (
        "kasia",
        "Kasia",
        "person.kasia",
        "user-kasia",
    )
    state = hass.states.get(SENSOR)
    assert state.state == "Kasia"
    assert state.attributes["score"] > 0.9
    assert "text" not in state.attributes  # no utterance text in the history
    assert events[0].data["speaker"] == "kasia"


async def test_unknown_speaker(hass: HomeAssistant, stack: Stack, entry: MockConfigEntry) -> None:
    stack.enroll("kasia", "Kasia", 300)
    await _setup(hass, entry)

    result = await _transcribe(hass, pcm(voice(900)))

    assert result.text == "jakie mam dzisiaj zajęcia"
    assert get_speaker(hass).speaker is None
    assert hass.states.get(SENSOR).state == "unknown"


async def test_speaker_does_not_leak_to_other_tasks(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    """Typed text (another task, no STT) has no speaker; max_age gives the last one."""
    stack.enroll("kasia", "Kasia", 300)
    await _setup(hass, entry)
    await _transcribe(hass, pcm(voice(300, seed=1)))
    assert get_speaker(hass) is not None

    other = await asyncio.get_running_loop().create_task(_in_new_context(hass))

    assert other == (None, "kasia")


async def _in_new_context(hass: HomeAssistant):
    CURRENT.set(None)  # a new task without STT
    last = get_speaker(hass, max_age=30)
    return get_speaker(hass), last.speaker if last else None


async def test_new_utterance_resets_speaker(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    stack.enroll("kasia", "Kasia", 300)
    await _setup(hass, entry)
    await _transcribe(hass, pcm(voice(300, seed=1)))
    await _transcribe(hass, pcm(voice(300, seconds=0.4)))  # too short
    assert get_speaker(hass).speaker is None


# --- conversation wrapper ---


def _target(hass: HomeAssistant) -> tuple[str, RecordingAgent]:
    """A separate agent (own config entry) the Assist Speaker ID agent passes requests to."""
    other = MockConfigEntry(domain="test_agent")
    other.add_to_hass(hass)
    agent = RecordingAgent()
    conversation.async_set_agent(hass, other, agent)
    return other.entry_id, agent


async def test_agent_passes_speaker_to_target(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    stack.enroll("jan", "Jan", 180)
    hass.states.async_set("person.jan", "home", {"user_id": "user-jan"})
    agent_id, target = _target(hass)
    await _setup(
        hass,
        entry,
        **{
            CONF_AGENT: agent_id,
            CONF_PROMPT: "Pytanie zadaje: {name}.",
            CONF_SET_USER: True,
            CONF_PEOPLE: {"jan": "person.jan"},
        },
    )

    await _transcribe(hass, pcm(voice(180, seed=2)))
    result = await conversation.async_converse(
        hass, "jakie mam dzisiaj zajęcia", None, Context(), language="pl", agent_id=AGENT
    )

    assert result.response.speech["plain"]["speech"] == "ok"
    got = target.inputs[-1]
    assert got.text == "jakie mam dzisiaj zajęcia"
    assert got.extra_system_prompt == "Pytanie zadaje: Jan."
    assert got.context.user_id == "user-jan"


async def test_agent_keeps_user_unless_enabled(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    stack.enroll("jan", "Jan", 180)
    hass.states.async_set("person.jan", "home", {"user_id": "user-jan"})
    agent_id, target = _target(hass)
    await _setup(hass, entry, **{CONF_AGENT: agent_id, CONF_PEOPLE: {"jan": "person.jan"}})

    await _transcribe(hass, pcm(voice(180, seed=2)))
    await conversation.async_converse(
        hass, "zapal światło", None, Context(), language="pl", agent_id=AGENT,
        extra_system_prompt="Jesteś asystentem.",
    )  # fmt: skip

    got = target.inputs[-1]
    assert got.extra_system_prompt == "Jesteś asystentem.\nThe person speaking is Jan."
    assert got.context.user_id is None


async def test_agent_without_speaker_changes_nothing(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    agent_id, target = _target(hass)
    await _setup(hass, entry, **{CONF_AGENT: agent_id})

    await conversation.async_converse(
        hass, "która godzina", None, Context(), language="pl", agent_id=AGENT,
        extra_system_prompt="bez zmian",
    )  # fmt: skip

    assert target.inputs[-1].extra_system_prompt == "bez zmian"
    assert target.inputs[-1].context.user_id is None


# --- services ---


async def test_enrollment_by_voice(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)

    started = await hass.services.async_call(
        DOMAIN, "start_enrollment", {"speaker": "tosia", "name": "Tosia", "count": 2},
        blocking=True, return_response=True,
    )  # fmt: skip
    await _transcribe(hass, pcm(voice(330, seed=1)))
    progress = hass.states.get(SENSOR).attributes["enrollment"]
    await _transcribe(hass, pcm(voice(330, seed=2)))
    await _transcribe(hass, pcm(voice(330, seed=3)))

    assert started == {"speaker": "tosia", "count": 2, "done": 0}
    assert progress == {"speaker": "tosia", "count": 2, "done": 1}
    assert hass.states.get(SENSOR).state == "Tosia"
    listed = await hass.services.async_call(
        DOMAIN, "list_speakers", {}, blocking=True, return_response=True
    )
    assert listed["speakers"][0]["samples"] == 2


async def test_calibrate_and_delete(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    stack.enroll("tata", "Tata", 110)
    stack.enroll("mama", "Mama", 220)
    await _setup(hass, entry)

    report = await hass.services.async_call(
        DOMAIN, "calibrate", {"apply": True}, blocking=True, return_response=True
    )
    await hass.services.async_call(DOMAIN, "delete_speaker", {"speaker": "mama"}, blocking=True)

    assert report["applied"] and report["speakers"]["tata"]["separable"]
    assert list(stack.service.store.speakers) == ["tata"]


# --- the real assist pipeline: STT and the agent in one run ---


async def test_pipeline_passes_speaker_from_stt_to_agent(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    """The ContextVar set by the STT entity reaches the conversation agent of the same
    pipeline run (HA runs speech_to_text and recognize_intent in one task)."""
    from homeassistant.components import assist_pipeline
    from homeassistant.components.assist_pipeline.pipeline import KEY_ASSIST_PIPELINE

    stack.enroll("kasia", "Kasia", 300)
    agent_id, target = _target(hass)
    await _setup(hass, entry, **{CONF_AGENT: agent_id, CONF_PROMPT: "Pytanie zadaje: {name}."})
    assert await async_setup_component(hass, "assist_pipeline", {})
    pipeline = await hass.data[KEY_ASSIST_PIPELINE].pipeline_store.async_create_item(
        {
            "conversation_engine": AGENT,
            "conversation_language": "pl",
            "language": "pl",
            "name": "Test",
            "stt_engine": STT,
            "stt_language": "pl",
            "tts_engine": None,
            "tts_language": None,
            "tts_voice": None,
            "wake_word_entity": None,
            "wake_word_id": None,
        }
    )
    events = []

    await assist_pipeline.async_pipeline_from_audio_stream(
        hass,
        context=Context(),
        event_callback=events.append,
        stt_metadata=METADATA,
        stt_stream=_stream(pcm(voice(300, seed=4))),
        pipeline_id=pipeline.id,
        end_stage=assist_pipeline.PipelineStage.INTENT,
    )

    assert [e.type for e in events if e.type.startswith("stt")][-1] == "stt-end"
    got = target.inputs[-1]
    assert got.text == "jakie mam dzisiaj zajęcia"
    assert got.extra_system_prompt == "Pytanie zadaje: Kasia."


# --- nagrywanie wzorca z przewodnikiem (satelita prosi o zdania sam) ---

SATELLITE = "assist_satellite.pokoj_jana"


def _satellite(hass: HomeAssistant, state: str) -> None:
    hass.states.async_set(SATELLITE, state, {"supported_features": 3})


async def _guided(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry, count: int = 2, **options
):
    from pytest_homeassistant_custom_component.common import async_mock_service

    await _setup(hass, entry, **options)
    hass.config.language = "pl"  # after setup: entity ids stay English
    _satellite(hass, "idle")
    asks = async_mock_service(hass, "assist_satellite", "start_conversation")
    says = async_mock_service(hass, "assist_satellite", "announce")
    await hass.services.async_call(
        DOMAIN, "start_enrollment",
        {"speaker": "jan", "name": "Jan", "count": count, "satellite": SATELLITE},
        blocking=True,
    )  # fmt: skip
    await hass.async_block_till_done()
    return asks, says


async def _turn(hass: HomeAssistant, audio: bytes | None) -> None:
    """The satellite listens; a sentence (or silence) comes; it answers and goes idle."""
    _satellite(hass, "listening")
    if audio is not None:
        await _transcribe(hass, audio)
    _satellite(hass, "responding")
    _satellite(hass, "idle")
    await hass.async_block_till_done()


async def test_guided_enrollment_asks_for_each_sample(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    asks, says = await _guided(hass, stack, entry, count=2)
    assert asks[0].data["entity_id"] == SATELLITE
    assert asks[0].data["start_message"].startswith("Nagrywam wzorzec głosu: Jan.")

    _satellite(hass, "idle")  # idle right after the message - not an answer yet
    await hass.async_block_till_done()
    assert len(asks) == 1

    await _turn(hass, pcm(voice(180, seed=1)))
    assert asks[-1].data["start_message"] == "Nagrano 1 z 2. Kolejne zdanie."
    await _turn(hass, pcm(voice(180, seconds=0.6)))  # too short - no sample
    assert asks[-1].data["start_message"].startswith("Za krótko.")
    await _turn(hass, pcm(voice(180, seed=2)))

    assert says[-1].data["message"] == "Gotowe, nagrano 2 próbek głosu: Jan."
    assert len(stack.service.store.speakers["jan"].samples) == 2
    assert entry.runtime_data.guide is not None and not entry.runtime_data.guide.active


async def test_guided_enrollment_stops_after_silence(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    asks, says = await _guided(hass, stack, entry, count=5)

    await _turn(hass, None)
    assert asks[-1].data["start_message"].startswith("Nic nie usłyszałem.")
    await _turn(hass, None)
    await _turn(hass, None)

    assert says[-1].data["message"] == "Kończę nagrywanie wzorca. Zapisałem 0 z 5."
    asked = len(asks)
    await _turn(hass, None)  # after finishing - no more questions
    assert len(asks) == asked


async def test_stop_enrollment_stops_guide(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    asks, _ = await _guided(hass, stack, entry)
    await hass.services.async_call(DOMAIN, "stop_enrollment", {}, blocking=True)
    await _turn(hass, pcm(voice(180, seed=1)))
    assert len(asks) == 1
    assert stack.service.enrollment is None


async def test_enrollment_reply_skips_the_agent(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    """Enrollment sample: the agent says „Nagrano 1 z 2” right away and keeps listening, the
    command does not reach the target agent; last sample - „Gotowe” once, no guide repeat."""
    agent_id, target = _target(hass)
    asks, says = await _guided(hass, stack, entry, count=2, **{CONF_AGENT: agent_id})

    async def sample(audio: bytes):
        _satellite(hass, "listening")
        await _transcribe(hass, audio)
        result = await conversation.async_converse(
            hass, "jaka będzie pogoda", None, Context(), language="pl", agent_id=AGENT
        )
        _satellite(hass, "responding")
        _satellite(hass, "idle")  # a moment of idle before listening again (bug from 2026-10-23)
        await hass.async_block_till_done()
        return result

    first = await sample(pcm(voice(180, seed=1)))
    short = await sample(pcm(voice(180, seconds=0.6)))
    last = await sample(pcm(voice(180, seed=2)))
    _satellite(hass, "idle")
    await hass.async_block_till_done()

    assert first.response.speech["plain"]["speech"] == "Nagrano 1 z 2. Kolejne zdanie."
    assert first.continue_conversation
    assert short.response.speech["plain"]["speech"].startswith("Za krótko.")
    assert short.continue_conversation
    assert last.response.speech["plain"]["speech"] == "Gotowe, nagrano 2 próbek głosu: Jan."
    assert not last.continue_conversation
    assert target.inputs == []  # not a single command reached the target agent
    assert says == []  # the guide does not repeat „Gotowe”
    assert len(asks) == 1  # only the first question - after that the conversation goes on by itself


async def test_normal_utterance_goes_to_agent(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    from custom_components.speaker_id.api import enrollment_reply

    stack.enroll("jan", "Jan", 180)
    await _setup(hass, entry)
    await _transcribe(hass, pcm(voice(180, seed=3)))
    assert enrollment_reply(hass) is None


async def test_guide_asks_once_when_conversation_really_ended(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    """After the agent's reply the satellite stops listening (conversation broke off): after 4 s
    one short „Kolejne zdanie.”, without repeating „Nagrano 1 z 3”."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    agent_id, _ = _target(hass)
    asks, _ = await _guided(hass, stack, entry, count=3, **{CONF_AGENT: agent_id})
    _satellite(hass, "listening")
    await _transcribe(hass, pcm(voice(180, seed=1)))
    reply = await conversation.async_converse(
        hass, "jaka pogoda", None, Context(), language="pl", agent_id=AGENT
    )
    _satellite(hass, "responding")
    _satellite(hass, "idle")
    await hass.async_block_till_done()
    assert reply.response.speech["plain"]["speech"] == "Nagrano 1 z 3. Kolejne zdanie."
    assert len(asks) == 1

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done()

    assert len(asks) == 2
    assert asks[-1].data["start_message"] == "Kolejne zdanie."


# --- options: recognition thresholds (stored in the service) ---


async def _options(hass: HomeAssistant, entry: MockConfigEntry, step: str):
    menu = await hass.config_entries.options.async_init(entry.entry_id)
    assert menu["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": step}
    )


async def test_thresholds_options_saved_in_service(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    stack.enroll("kasia", "Kasia", 300)
    stack.enroll("tata", "Tata", 110)
    stack.service.store.upsert("tata", None, 0.7)  # own threshold from calibration
    await _setup(hass, entry)

    form = await _options(hass, entry, "thresholds")
    assert form["step_id"] == "thresholds"
    assert "- Tata: 3 · 0.70" in form["description_placeholders"]["speakers"]
    assert "- Kasia: 3 · —" in form["description_placeholders"]["speakers"]

    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {"threshold": 0.6, "margin": 0.08, "min_speech": 1.2, "Kasia (kasia)": 0.72},
    )

    assert result["type"] is FlowResultType.ABORT and result["reason"] == "thresholds_saved"
    store = stack.service.store
    assert store.settings() == {"threshold": 0.6, "margin": 0.08, "min_speech": 1.2}
    assert store.speakers["kasia"].threshold == 0.72
    assert store.speakers["tata"].threshold is None  # empty field = shared threshold


async def test_people_options_still_work(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    stack.enroll("kasia", "Kasia", 300)
    await _setup(hass, entry)
    hass.config.language = "pl"
    form = await _options(hass, entry, "people")
    fields = [str(k) for k in form["data_schema"].schema]
    assert "Kasia (kasia)" in fields  # name instead of the internal key
    prompt = next(k for k in form["data_schema"].schema if str(k) == CONF_PROMPT)
    assert prompt.description["suggested_value"] == "Pyta: {name}."  # Polish by default
    assert form["description_placeholders"]["name"] == "{name}"  # literally in the description
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {CONF_PROMPT: "Mówi: {name}.", "Kasia (kasia)": "person.kasia"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_PEOPLE] == {"kasia": "person.kasia"}
    await hass.async_block_till_done()  # new options reload the integration


def test_prompt_for_translates_untouched_default() -> None:
    from custom_components.speaker_id.const import prompt_for

    assert prompt_for("The person speaking is {name}.", "pl") == "Pyta: {name}."
    assert prompt_for(None, "pl") == "Pyta: {name}."
    assert prompt_for("Mówi do ciebie {name}.", "en") == "Mówi do ciebie {name}."  # custom


# --- announcement (intercom): the speaker's recording on the other speakers ---


async def test_announce_last_plays_the_recording(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry, hass_client
) -> None:
    from pytest_homeassistant_custom_component.common import async_mock_service

    from custom_components.speaker_id.api import async_announce_last

    await _setup(hass, entry)
    for name, features in (("kuchnia", 3), ("salon", 1), ("sypialnia", 0)):
        hass.states.async_set(f"assist_satellite.{name}", "idle", {"supported_features": features})
    hass.states.async_set("assist_satellite.lazienka", "unavailable", {"supported_features": 3})
    announce = async_mock_service(hass, "assist_satellite", "announce")
    play = async_mock_service(hass, "media_player", "play_media")
    audio = pcm(voice(220, seconds=2.0))
    await _transcribe(hass, audio)  # „Ogłoszenie, dzieci, obiad” - the service keeps the recording

    done = await async_announce_last(
        hass, exclude="assist_satellite.kuchnia", media_players=["media_player.homepod"]
    )
    await hass.async_block_till_done()

    # the kitchen recorded it, the bedroom does not announce, the bathroom is unavailable
    assert done == ["assist_satellite.salon", "media_player.homepod"]
    assert announce[0].data["entity_id"] == ["assist_satellite.salon"]
    path = announce[0].data["media_id"]
    assert path.startswith("/api/speaker_id/announcement/")
    assert play[0].data["announce"] is True
    client = await hass_client()
    resp = await client.get(path)
    assert resp.status == 200
    assert resp.content_type == "audio/wav"
    body = await resp.read()
    assert body[:4] == b"RIFF" and len(body) > len(audio) // 2  # the whole utterance


async def test_announce_prefers_players_per_room(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    """prefer_players: one speaker per room - an available player instead of the
    satellite; a player that is "off" (disconnected HomePod) leaves it to the satellite;
    the announcing room gets nothing."""
    from homeassistant.helpers import area_registry as ar, entity_registry as er
    from pytest_homeassistant_custom_component.common import async_mock_service

    from custom_components.speaker_id.api import async_announce_last

    await _setup(hass, entry)
    areas = ar.async_get(hass)
    entities = er.async_get(hass)
    for room in ("kuchnia", "salon", "sypialnia"):
        area = areas.async_create(room)
        for domain, state in (("assist_satellite", "idle"), ("media_player", "idle")):
            ent = entities.async_get_or_create(domain, "test", room, suggested_object_id=room)
            entities.async_update_entity(ent.entity_id, area_id=area.id)
            hass.states.async_set(ent.entity_id, state, {"supported_features": 3})
    hass.states.async_set("media_player.sypialnia", "off")  # HomePod not connected
    announce = async_mock_service(hass, "assist_satellite", "announce")
    play = async_mock_service(hass, "media_player", "play_media")
    await _transcribe(hass, pcm(voice(220, seconds=2.0)))

    done = await async_announce_last(
        hass,
        exclude="assist_satellite.kuchnia",
        media_players=["media_player.kuchnia", "media_player.salon", "media_player.sypialnia"],
        prefer_players=True,
    )
    await hass.async_block_till_done()

    assert done == ["assist_satellite.sypialnia", "media_player.salon"]
    assert announce[0].data["entity_id"] == ["assist_satellite.sypialnia"]
    assert play[0].data["entity_id"] == ["media_player.salon"]


async def test_announce_without_recording(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry
) -> None:
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.speaker_id.api import async_announce_last

    await _setup(hass, entry)
    assert await async_announce_last(hass) is None  # text typed by hand - no recording
    with pytest.raises(HomeAssistantError, match="No recording"):
        await hass.services.async_call(DOMAIN, "announce_last", {}, blocking=True)


async def test_unknown_announcement_token(
    hass: HomeAssistant, stack: Stack, entry: MockConfigEntry, hass_client
) -> None:
    await _setup(hass, entry)
    client = await hass_client()
    assert (await client.get("/api/speaker_id/announcement/nie-ma")).status == 404
