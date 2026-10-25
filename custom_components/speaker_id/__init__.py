"""Assist Speaker ID: who is talking to Assist (wyoming-speaker-id)."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .announcement import async_announce, async_setup_announcements
from .client import SpeakerIdClient, SpeakerIdError
from .const import (
    CONF_API_PORT,
    CONF_API_TOKEN,
    CONF_PEOPLE,
    DOMAIN,
    EVENT_IDENTIFIED,
    SIGNAL_RESULT,
)
from .guide import Guide
from .speaker import SpeakerResult

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CONVERSATION, Platform.SENSOR, Platform.STT]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SPEAKER = vol.All(cv.string, vol.Match(r"^[a-z0-9_]{1,32}$"))


@dataclass
class RuntimeData:
    client: SpeakerIdClient
    asr_name: str
    languages: list[str]
    last: SpeakerResult | None = None
    guide: Guide | None = None  # nagrywanie wzorca z przewodnikiem (satelita pyta sam)
    enrolled: int = 0  # samples of the current enrollment (did this utterance add one)


type SpeakerIdConfigEntry = ConfigEntry[RuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    _register_services(hass)
    async_setup_announcements(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SpeakerIdConfigEntry) -> bool:
    client = SpeakerIdClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_API_PORT],
        entry.data.get(CONF_API_TOKEN),
    )
    try:
        info = await client.describe()
    except SpeakerIdError as err:
        raise ConfigEntryNotReady(str(err)) from err
    if not info.asr:
        raise ConfigEntryNotReady("the service reports no speech-to-text program")
    asr = info.asr[0]
    languages = sorted({lang for m in asr.models if m.installed for lang in m.languages})
    entry.runtime_data = RuntimeData(client, asr.name, languages)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SpeakerIdConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _options_updated(hass: HomeAssistant, entry: SpeakerIdConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def async_handle_result(
    hass: HomeAssistant, entry: SpeakerIdConfigEntry, result: SpeakerResult
) -> SpeakerResult:
    """Add the mapped person and user, remember, announce (event, sensor)."""
    person = entry.options.get(CONF_PEOPLE, {}).get(result.speaker or "")
    user_id = None
    if person and (state := hass.states.get(person)):
        user_id = state.attributes.get("user_id")
    added = None
    if result.enrollment is not None:
        done = int(result.enrollment.get("done", 0))
        added = done > entry.runtime_data.enrolled
        entry.runtime_data.enrolled = done
    result = replace(result, person=person, user_id=user_id, sample_added=added)
    entry.runtime_data.last = result
    if entry.runtime_data.guide is not None:
        entry.runtime_data.guide.on_result(result)
    hass.bus.async_fire(EVENT_IDENTIFIED, {"entry_id": entry.entry_id, **result.as_dict()})
    async_dispatcher_send(hass, SIGNAL_RESULT.format(entry_id=entry.entry_id), result)
    return result


def device_info(entry: SpeakerIdConfigEntry) -> DeviceInfo:
    """One service device for the STT, agent and sensor entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Assist Speaker ID",
        manufacturer="wyoming-speaker-id",
        model=entry.runtime_data.asr_name,
        entry_type=DeviceEntryType.SERVICE,
    )


def _entry(hass: HomeAssistant) -> SpeakerIdConfigEntry:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("Assist Speaker ID is not set up")
    return entries[0]


def _client(hass: HomeAssistant) -> SpeakerIdClient:
    return _entry(hass).runtime_data.client


def _register_services(hass: HomeAssistant) -> None:
    async def call(path: str, method: str, json: Any = None) -> Any:
        try:
            return await _client(hass).api(method, path, json)
        except SpeakerIdError as err:
            raise HomeAssistantError(str(err)) from err

    async def start_enrollment(service: ServiceCall) -> ServiceResponse:
        speaker, count = service.data["speaker"], service.data["count"]
        body = {"speaker": speaker, "count": count}
        if name := service.data.get("name"):
            body["name"] = name
        response = await call("enrollment", "POST", body)
        runtime = _entry(hass).runtime_data
        runtime.enrolled = 0
        if runtime.guide is not None:
            runtime.guide.stop()
            runtime.guide = None
        if satellite := service.data.get("satellite"):
            runtime.guide = Guide(hass, satellite, speaker, name or speaker, count)
            await runtime.guide.async_start()
        return response

    async def stop_enrollment(service: ServiceCall) -> ServiceResponse:
        runtime = _entry(hass).runtime_data
        if runtime.guide is not None:
            runtime.guide.stop()
            runtime.guide = None
        return await call("enrollment", "DELETE")

    async def add_last_sample(service: ServiceCall) -> ServiceResponse:
        return await call(f"speakers/{service.data['speaker']}/samples/last", "POST")

    async def delete_speaker(service: ServiceCall) -> ServiceResponse:
        return await call(f"speakers/{service.data['speaker']}", "DELETE")

    async def set_speaker(service: ServiceCall) -> ServiceResponse:
        body = {"name": service.data.get("name"), "threshold": service.data.get("threshold")}
        return await call(f"speakers/{service.data['speaker']}", "PUT", body)

    async def list_speakers(service: ServiceCall) -> ServiceResponse:
        return {"speakers": await call("speakers", "GET")}

    async def announce_last(service: ServiceCall) -> ServiceResponse:
        """The last utterance (the speaker's own recording) on other speakers."""
        try:
            done = await async_announce(
                hass,
                _client(hass),
                exclude=service.data.get("exclude"),
                satellites=service.data.get("satellites"),
                media_players=service.data.get("media_players"),
                preannounce=service.data["preannounce"],
                max_age=service.data["max_age"],
                prefer_players=service.data["prefer_players"],
            )
        except LookupError as err:
            raise HomeAssistantError(f"No recording to announce: {err}") from err
        except SpeakerIdError as err:
            raise HomeAssistantError(str(err)) from err
        return {"satellites": done.satellites, "media_players": done.media_players}

    async def calibrate(service: ServiceCall) -> ServiceResponse:
        suffix = "?apply=1" if service.data.get("apply") else ""
        return await call(f"calibrate{suffix}", "POST")

    only_speaker = vol.Schema({vol.Required("speaker"): SPEAKER})
    services = {
        "start_enrollment": (
            start_enrollment,
            vol.Schema(
                {
                    vol.Required("speaker"): SPEAKER,
                    vol.Optional("name"): cv.string,
                    vol.Optional("count", default=10): vol.All(int, vol.Range(1, 50)),
                    vol.Optional("satellite"): cv.entity_domain("assist_satellite"),
                }
            ),
        ),
        "stop_enrollment": (stop_enrollment, vol.Schema({})),
        "add_last_sample": (add_last_sample, only_speaker),
        "delete_speaker": (delete_speaker, only_speaker),
        "set_speaker": (
            set_speaker,
            vol.Schema(
                {
                    vol.Required("speaker"): SPEAKER,
                    vol.Optional("name"): cv.string,
                    vol.Optional("threshold"): vol.Any(None, vol.Coerce(float)),
                }
            ),
        ),
        "list_speakers": (list_speakers, vol.Schema({})),
        "announce_last": (
            announce_last,
            vol.Schema(
                {
                    vol.Optional("satellites"): cv.entity_ids,
                    vol.Optional("media_players"): cv.entity_ids,
                    vol.Optional("exclude"): cv.entity_id,
                    vol.Optional("preannounce", default=True): bool,
                    vol.Optional("prefer_players", default=False): bool,
                    vol.Optional("max_age", default=60): vol.All(
                        vol.Coerce(float), vol.Range(1, 600)
                    ),
                }
            ),
        ),
        "calibrate": (calibrate, vol.Schema({vol.Optional("apply", default=False): bool})),
    }
    for name, (handler, schema) in services.items():
        hass.services.async_register(
            DOMAIN, name, handler, schema=schema, supports_response=SupportsResponse.OPTIONAL
        )
