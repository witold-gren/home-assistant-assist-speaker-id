"""Conversation agent wrapper: tells any agent who is speaking.

Pipelines use "Assist Speaker ID" as their conversation agent; it adds a line like "The
person speaking is Anna." to the extra system prompt (LLM agents read it), optionally
runs the request as that person's Home Assistant user, and passes everything on to
the configured agent. A probable speaker (not certain) only gets a "probably Anna" line.
"""

from __future__ import annotations

from typing import Literal

from homeassistant.components import conversation
from homeassistant.const import MATCH_ALL
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpeakerIdConfigEntry, device_info
from .api import enrollment_reply
from .const import CONF_AGENT, CONF_PROMPT, CONF_SET_USER, ENTITY_IDS, probable_prompt, prompt_for
from .speaker import CURRENT


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpeakerIdConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([SpeakerIdAgent(entry)])


class SpeakerIdAgent(conversation.ConversationEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "agent"

    def __init__(self, entry: SpeakerIdConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-conversation"
        self.entity_id = ENTITY_IDS["conversation"]  # the same in every HA language
        self._attr_device_info = device_info(entry)

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return MATCH_ALL

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        if (reply := enrollment_reply(self.hass)) is not None:
            # enrollment: a short message instead of a command, then the next sentence right away
            response = intent.IntentResponse(language=user_input.language)
            response.async_set_speech(reply[0])
            return conversation.ConversationResult(
                response=response,
                conversation_id=user_input.conversation_id,
                continue_conversation=reply[1],
            )
        options = self._entry.options
        target = options.get(CONF_AGENT) or conversation.HOME_ASSISTANT_AGENT
        if target == self.entity_id:
            target = conversation.HOME_ASSISTANT_AGENT
        context = user_input.context
        prompt = user_input.extra_system_prompt
        speaker = CURRENT.get()
        if speaker is not None and speaker.speaker:
            template = prompt_for(options.get(CONF_PROMPT), self.hass.config.language)
            line = template.format(name=speaker.name or speaker.speaker)
            prompt = f"{prompt}\n{line}" if prompt else line
            if options.get(CONF_SET_USER) and speaker.user_id and not context.user_id:
                context = Context(user_id=speaker.user_id, parent_id=context.id)
        elif speaker is not None and speaker.probable:
            # not certain - the agent is told so, and nothing runs as that person's user
            line = probable_prompt(self.hass.config.language).format(
                name=speaker.probable_name or speaker.probable
            )
            prompt = f"{prompt}\n{line}" if prompt else line
        return await conversation.async_converse(
            self.hass,
            user_input.text,
            user_input.conversation_id,
            context,
            language=user_input.language,
            agent_id=target,
            device_id=user_input.device_id,
            satellite_id=user_input.satellite_id,
            extra_system_prompt=prompt,
        )
