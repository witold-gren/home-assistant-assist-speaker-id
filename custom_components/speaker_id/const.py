"""Constants for Assist Speaker ID."""

DOMAIN = "speaker_id"

CONF_API_PORT = "api_port"
CONF_API_TOKEN = "api_token"
CONF_AGENT = "agent_id"
CONF_PEOPLE = "people"  # speaker id -> person entity id
CONF_SET_USER = "set_user"
CONF_PROMPT = "prompt"
# lowest similarity at which an unrecognized voice is still reported as "probably <person>"
CONF_PROBABLE_THRESHOLD = "probable_threshold"
DEFAULT_PROBABLE_THRESHOLD = 0.4

DEFAULT_PORT = 10350
DEFAULT_API_PORT = 10351
# line for the agent (LLM) in the HA language; {name} = the speaker's name
PROMPTS = {"pl": "Pyta: {name}.", "en": "The person speaking is {name}."}
# the same for a probable (not certain) speaker
PROBABLE_PROMPTS = {
    "pl": "Prawdopodobnie pyta: {name} - nie ma pewności, powiedz na początku, kogo rozpoznajesz.",
    "en": "The person speaking is probably {name} - not certain, say first who you recognize.",
}


def default_prompt(language: str | None) -> str:
    return PROMPTS.get((language or "en").split("-")[0], PROMPTS["en"])


def probable_prompt(language: str | None) -> str:
    return PROBABLE_PROMPTS.get((language or "en").split("-")[0], PROBABLE_PROMPTS["en"])


def prompt_for(stored: str | None, language: str | None) -> str:
    """The saved line; an unchanged default (from any language) follows the HA language."""
    if not stored or stored in PROMPTS.values():
        return default_prompt(language)
    return stored


EVENT_IDENTIFIED = f"{DOMAIN}_identified"
SIGNAL_RESULT = f"{DOMAIN}_result_{{entry_id}}"

# entity ids independent of the HA language (key = unique id suffix)
ENTITY_IDS = {
    "last_speaker": "sensor.speaker_id_last_speaker",
    "stt": "stt.speaker_id_speech_to_text",
    "conversation": "conversation.speaker_id_agent",
}
# default ids of earlier versions, built by HA from the translated name - renamed on setup
LEGACY_OBJECT_IDS = {
    "last_speaker": (
        "speaker_id_ostatni_mowca",
        "assist_speaker_id_ostatni_mowca",
        "assist_speaker_id_last_speaker",
    ),
    "stt": (
        "speaker_id_mowa_na_tekst_z_mowca",
        "assist_speaker_id_mowa_na_tekst_z_mowca",
        "assist_speaker_id_speech_to_text_with_speaker",
    ),
    "conversation": (
        "speaker_id_agent_z_mowca",
        "assist_speaker_id_agent_z_mowca",
        "assist_speaker_id_agent_with_speaker",
    ),
}
