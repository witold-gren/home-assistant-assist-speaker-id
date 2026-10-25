"""Speech to text through wyoming-speaker-id: the transcript plus who spoke."""

from __future__ import annotations

from collections.abc import AsyncIterable
import logging

from homeassistant.components import stt
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpeakerIdConfigEntry, async_handle_result, device_info
from .client import SpeakerIdError
from .const import ENTITY_IDS
from .speaker import CURRENT, SpeakerResult

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpeakerIdConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([SpeakerIdStt(entry)])


class SpeakerIdStt(stt.SpeechToTextEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "stt"

    def __init__(self, entry: SpeakerIdConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-stt"
        self.entity_id = ENTITY_IDS["stt"]  # the same in every HA language
        self._attr_device_info = device_info(entry)
        self._attr_extra_state_attributes = {"asr": entry.runtime_data.asr_name}

    @property
    def supported_languages(self) -> list[str]:
        return self._entry.runtime_data.languages

    @property
    def supported_formats(self) -> list[stt.AudioFormats]:
        return [stt.AudioFormats.WAV]

    @property
    def supported_codecs(self) -> list[stt.AudioCodecs]:
        return [stt.AudioCodecs.PCM]

    @property
    def supported_bit_rates(self) -> list[stt.AudioBitRates]:
        return [stt.AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self) -> list[stt.AudioSampleRates]:
        return [stt.AudioSampleRates.SAMPLERATE_16000]

    @property
    def supported_channels(self) -> list[stt.AudioChannels]:
        return [stt.AudioChannels.CHANNEL_MONO]

    async def async_process_audio_stream(
        self, metadata: stt.SpeechMetadata, stream: AsyncIterable[bytes]
    ) -> stt.SpeechResult:
        # nothing from an earlier utterance may leak into this run
        CURRENT.set(None)
        try:
            answer = await self._entry.runtime_data.client.transcribe(metadata.language, stream)
        except (OSError, SpeakerIdError) as err:
            _LOGGER.error("Speech to text failed: %s", err)
            return stt.SpeechResult(None, stt.SpeechResultState.ERROR)
        if answer is None:
            return stt.SpeechResult(None, stt.SpeechResultState.ERROR)
        text, data = answer
        result = SpeakerResult.from_transcript(data)
        if result is not None:
            # the conversation agent runs later in the same pipeline task and reads it
            CURRENT.set(async_handle_result(self.hass, self._entry, result))
        return stt.SpeechResult(text, stt.SpeechResultState.SUCCESS)
