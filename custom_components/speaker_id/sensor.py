"""Last speaker sensor (state: name, or "unknown"; scores and enrollment as attributes).

The recognised text is deliberately not stored - the recorder keeps sensor history.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpeakerIdConfigEntry, device_info
from .const import SIGNAL_RESULT
from .speaker import SpeakerResult

UNKNOWN = "unknown"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpeakerIdConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([LastSpeakerSensor(entry)])


class LastSpeakerSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "last_speaker"
    _attr_should_poll = False

    def __init__(self, entry: SpeakerIdConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-last_speaker"
        self._attr_device_info = device_info(entry)
        self._result: SpeakerResult | None = None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_RESULT.format(entry_id=self._entry.entry_id), self._update
            )
        )

    @callback
    def _update(self, result: SpeakerResult) -> None:
        self._result = result
        self.async_write_ha_state()

    @property
    def native_value(self) -> str | None:
        if self._result is None:
            return None
        return self._result.name or self._result.speaker or UNKNOWN

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self._result.as_dict() if self._result else None
