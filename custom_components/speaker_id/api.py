"""Public helper for other integrations (e.g. a conversation agent).

    try:
        from custom_components.speaker_id.api import get_speaker
    except ImportError:
        get_speaker = None
    ...
    speaker = get_speaker(hass) if get_speaker else None
    if speaker and speaker.speaker:
        ...  # speaker.speaker (id), speaker.name, speaker.person, speaker.user_id, speaker.score

Inside a voice pipeline run it returns the speaker of the current utterance; for typed
text (no speech-to-text in this run) it returns None. `max_age` additionally accepts
the last identification of any run if it is at most that many seconds old.
"""

from __future__ import annotations

import time

from homeassistant.core import HomeAssistant

from .client import SpeakerIdError
from .const import DOMAIN
from .guide import MESSAGES
from .speaker import CURRENT, SpeakerResult


def get_speaker(hass: HomeAssistant, max_age: float | None = None) -> SpeakerResult | None:
    current = CURRENT.get()
    if current is not None or max_age is None:
        return current
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        last = entry.runtime_data.last
        if last is not None and time.monotonic() - last.at <= max_age:
            return last
    return None


def enrollment_reply(hass: HomeAssistant) -> tuple[str, bool] | None:
    """Agent reply to an utterance during enrollment: (text, keep listening).

    The conversation agent calls this first and - when it does not return None - says only
    this text, without handling a command and without an LLM: „Nagrano 3 z 10. Kolejne
    zdanie.”, and the satellite listens for the next sentence right away. None = a normal
    utterance.
    """
    result = CURRENT.get()
    if result is None or result.sample_added is None or result.enrollment is None:
        return None
    done = int(result.enrollment.get("done", 0))
    count = int(result.enrollment.get("count", 0))
    language = (hass.config.language or "en").split("-")[0]
    text = MESSAGES.get(language, MESSAGES["en"])
    finished = bool(result.sample_added and done >= count)
    name = str(result.enrollment.get("speaker", "")).title()
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if (guide := entry.runtime_data.guide) is not None and guide.active:
            guide.agent_replied(finished)  # the guide will not repeat the message
            name = guide.name
    if not result.sample_added:
        return text["short"], True
    if finished:
        return text["done"].format(name=name, done=done, count=count), False
    return text["sample"].format(name=name, done=done, count=count), True


async def async_announce_last(
    hass: HomeAssistant,
    *,
    exclude: str | None = None,
    media_players: list[str] | None = None,
    preannounce: bool = True,
    prefer_players: bool = False,
) -> list[str] | None:
    """Play the last utterance (the speaker's recording, unchanged) on the other speakers.

    For the conversation agent: „Ogłoszenie, dzieci, obiad!” - the satellites (except
    ``exclude``, the one that recorded it) and ``media_players`` play the recording instead
    of text to speech. Returns the entities it played on; None when there is no fresh
    recording (e.g. text typed by hand) - then the agent can read the text with text to
    speech.
    """
    from .announcement import async_announce

    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        try:
            done = await async_announce(
                hass,
                entry.runtime_data.client,
                exclude=exclude,
                media_players=media_players,
                preannounce=preannounce,
                prefer_players=prefer_players,
            )
        except (LookupError, SpeakerIdError):
            return None
        return done.satellites + done.media_players
    return None
