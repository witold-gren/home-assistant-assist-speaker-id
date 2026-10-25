"""Constants for Assist Speaker ID."""

DOMAIN = "speaker_id"

CONF_API_PORT = "api_port"
CONF_API_TOKEN = "api_token"
CONF_AGENT = "agent_id"
CONF_PEOPLE = "people"  # speaker id -> person entity id
CONF_SET_USER = "set_user"
CONF_PROMPT = "prompt"

DEFAULT_PORT = 10350
DEFAULT_API_PORT = 10351
# line for the agent (LLM) in the HA language; {name} = the speaker's name
PROMPTS = {"pl": "Pyta: {name}.", "en": "The person speaking is {name}."}


def default_prompt(language: str | None) -> str:
    return PROMPTS.get((language or "en").split("-")[0], PROMPTS["en"])


def prompt_for(stored: str | None, language: str | None) -> str:
    """The saved line; an unchanged default (from any language) follows the HA language."""
    if not stored or stored in PROMPTS.values():
        return default_prompt(language)
    return stored


EVENT_IDENTIFIED = f"{DOMAIN}_identified"
SIGNAL_RESULT = f"{DOMAIN}_result_{{entry_id}}"
