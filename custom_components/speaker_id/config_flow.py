"""Config flow: the service (manual or add-on discovery) and options (agent, people)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
import voluptuous as vol

from . import SpeakerIdConfigEntry
from .client import SpeakerIdClient, SpeakerIdError
from .const import (
    CONF_AGENT,
    CONF_API_PORT,
    CONF_API_TOKEN,
    CONF_PEOPLE,
    CONF_PROBABLE_THRESHOLD,
    CONF_PROMPT,
    CONF_SET_USER,
    DEFAULT_API_PORT,
    DEFAULT_PORT,
    DEFAULT_PROBABLE_THRESHOLD,
    DOMAIN,
    prompt_for,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_API_PORT, default=DEFAULT_API_PORT): int,
        vol.Optional(CONF_API_TOKEN): str,
    }
)


class SpeakerIdConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, Any] = {}

    async def _validate(self, data: dict[str, Any]) -> str | None:
        """Error key, or None when both the Wyoming and the HTTP side answer."""
        client = SpeakerIdClient(
            async_get_clientsession(self.hass),
            data[CONF_HOST],
            data[CONF_PORT],
            data[CONF_API_PORT],
            data.get(CONF_API_TOKEN),
        )
        try:
            info = await client.describe()
            await client.api("GET", "info")
        except SpeakerIdError as err:
            _LOGGER.warning("Assist Speaker ID service check failed: %s", err)
            return "invalid_auth" if "HTTP 401" in str(err) else "cannot_connect"
        return None if info.asr else "no_asr"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()
            if (error := await self._validate(user_input)) is None:
                return self.async_create_entry(title="Assist Speaker ID", data=user_input)
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(SERVICE_SCHEMA, user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change host, ports or the API token without losing the options (people, agent)."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            # empty token field = no token (the old one is not kept)
            data = {**user_input, CONF_API_TOKEN: user_input.get(CONF_API_TOKEN) or None}
            if (error := await self._validate(data)) is None:
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                SERVICE_SCHEMA, user_input or dict(entry.data)
            ),
            errors=errors,
        )

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        """The add-on announces itself (host, ports, API token)."""
        config = discovery_info.config
        self._discovered = {
            CONF_HOST: config["host"],
            CONF_PORT: int(config.get("port", DEFAULT_PORT)),
            CONF_API_PORT: int(config.get("api_port", DEFAULT_API_PORT)),
            CONF_API_TOKEN: config.get("api_token"),
        }
        await self.async_set_unique_id(discovery_info.uuid)
        self._abort_if_unique_id_configured(updates=self._discovered)
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if (error := await self._validate(self._discovered)) is None:
                return self.async_create_entry(title="Assist Speaker ID", data=self._discovered)
            errors["base"] = error
        return self.async_show_form(step_id="hassio_confirm", errors=errors)

    @staticmethod
    def async_get_options_flow(entry: SpeakerIdConfigEntry) -> OptionsFlow:
        return SpeakerIdOptionsFlow()


def _slider(minimum: float, maximum: float, step: float, unit: str | None = None):
    config = selector.NumberSelectorConfig(
        min=minimum, max=maximum, step=step, mode=selector.NumberSelectorMode.SLIDER
    )
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


class SpeakerIdOptionsFlow(OptionsFlow):
    """Menu: agent and people (HA options) / recognition thresholds (stored in the service)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["people", "thresholds"])

    @property
    def _probable_threshold(self) -> float:
        return self.config_entry.options.get(CONF_PROBABLE_THRESHOLD, DEFAULT_PROBABLE_THRESHOLD)

    async def async_step_thresholds(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Shared threshold, margin and minimum speech, plus an own threshold per person
        (empty = the shared one). Saved straight to the service, effective immediately."""
        client = self.config_entry.runtime_data.client
        errors: dict[str, str] = {}
        try:
            settings = await client.api("GET", "settings")
            speakers = await client.api("GET", "speakers")
        except SpeakerIdError:
            return self.async_abort(reason="cannot_connect")
        own = {f"{s['name']} ({s['id']})": s for s in speakers}  # pole formularza -> osoba
        if user_input is not None:
            try:
                await client.api(
                    "PUT",
                    "settings",
                    {k: user_input[k] for k in ("threshold", "margin", "min_speech")},
                )
                for key, speaker in own.items():
                    value = user_input.get(key)
                    if value != speaker["threshold"]:
                        await client.api("PUT", f"speakers/{speaker['id']}", {"threshold": value})
            except SpeakerIdError:
                errors["base"] = "cannot_connect"
            else:
                probable = user_input[CONF_PROBABLE_THRESHOLD]
                if probable != self._probable_threshold:
                    # kept in Home Assistant (not in the service); reloads the integration
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        options={**self.config_entry.options, CONF_PROBABLE_THRESHOLD: probable},
                    )
                return self.async_abort(reason="thresholds_saved")
        fields: dict[Any, Any] = {
            vol.Required("threshold"): _slider(0.3, 0.95, 0.01),
            vol.Required("margin"): _slider(0.0, 0.3, 0.01),
            vol.Required("min_speech"): _slider(0.5, 3.0, 0.1, "s"),
            vol.Required(CONF_PROBABLE_THRESHOLD): _slider(0.2, 0.9, 0.01),
        }
        for key in own:
            fields[vol.Optional(key)] = _slider(0.3, 0.95, 0.01)
        suggested = {
            **settings,
            CONF_PROBABLE_THRESHOLD: self._probable_threshold,
            **{k: s["threshold"] for k, s in own.items() if s["threshold"]},
        }
        return self.async_show_form(
            step_id="thresholds",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(fields), suggested),
            errors=errors,
            description_placeholders={
                "speakers": "\n".join(
                    f"- {s['name']}: {s['samples']} ({s.get('recordings', 0)}) · "
                    + (f"{s['threshold']:.2f}" if s["threshold"] else "—")
                    for s in speakers
                )
                or "-"
            },
        )

    async def async_step_people(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Wrapped agent, prompt line, and which HA person each enrolled voice is."""
        entry: SpeakerIdConfigEntry = self.config_entry
        try:
            speakers = await entry.runtime_data.client.api("GET", "speakers")
        except SpeakerIdError:
            speakers = []
        # per-person fields built on the fly: the label is the name
        # (dynamic fields cannot be translated)
        voices = {f"{s['name']} ({s['id']})": s["id"] for s in speakers}
        if user_input is not None:
            people = {voices[k]: v for k, v in user_input.items() if k in voices and v}
            options = {k: v for k, v in user_input.items() if k not in voices}
            kept = {k: v for k, v in entry.options.items() if k == CONF_PROBABLE_THRESHOLD}
            return self.async_create_entry(data={**kept, **options, CONF_PEOPLE: people})

        current = entry.options
        people = current.get(CONF_PEOPLE, {})
        fields: dict[Any, Any] = {
            vol.Optional(CONF_AGENT): selector.ConversationAgentSelector(),
            vol.Optional(CONF_PROMPT): str,
            vol.Optional(CONF_SET_USER, default=False): bool,
        }
        for key in voices:
            fields[vol.Optional(key)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="person")
            )
        suggested = {
            **{k: v for k, v in current.items() if k != CONF_PEOPLE},
            CONF_PROMPT: prompt_for(current.get(CONF_PROMPT), self.hass.config.language),
            **{key: people[sid] for key, sid in voices.items() if sid in people},
        }
        return self.async_show_form(
            step_id="people",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(fields), suggested),
            description_placeholders={
                "speakers": ", ".join(f"{s['name']} ({s['samples']})" for s in speakers) or "-",
                # a literal {name} in the description -
                # the frontend would take it for a translation variable
                "name": "{name}",
            },
        )
