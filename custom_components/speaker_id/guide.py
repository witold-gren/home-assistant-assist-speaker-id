"""Guided enrollment: the satellite asks for each sample itself.

`speaker_id.start_enrollment` with `satellite` starts a conversation on that voice
satellite ("Say a normal command…"). Each answer goes through the usual pipeline (and
becomes a sample); when the satellite is idle again it asks for the next one, until
all samples are recorded. No wake word needed.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

if TYPE_CHECKING:
    from .speaker import SpeakerResult

_LOGGER = logging.getLogger(__name__)

# no new sample after this many prompts in a row (silence, too short) -> stop
MAX_MISSES = 3
# after the agent's reply the satellite may pass through idle before listening again;
# only if it is still idle after this long the conversation really ended
RESUME_SECONDS = 4.0

MESSAGES = {
    "pl": {
        "start": "Nagrywam wzorzec głosu: {name}. Powiedz zwykłe polecenie, na przykład: "
        "jaka będzie jutro pogoda.",
        "next": "Nagrano {done} z {count}. Kolejne zdanie.",
        "sample": "Nagrano {done} z {count}. Kolejne zdanie.",
        "short": "Za krótko. Powiedz dłuższe zdanie, kilka słów.",
        "silence": "Nic nie usłyszałem. Powiedz zdanie, kilka słów.",
        "continue": "Kolejne zdanie.",
        "done": "Gotowe, nagrano {count} próbek głosu: {name}.",
        "stopped": "Kończę nagrywanie wzorca. Zapisałem {done} z {count}.",
    },
    "en": {
        "start": "Recording a voiceprint for {name}. Say a normal command, for example: "
        "what is the weather tomorrow.",
        "next": "Recorded {done} of {count}. Next sentence.",
        "sample": "Recorded {done} of {count}. Next sentence.",
        "short": "Too short. Please say a longer sentence.",
        "silence": "I did not hear anything. Please say a sentence.",
        "continue": "Next sentence.",
        "done": "Done, recorded {count} voice samples for {name}.",
        "stopped": "Stopping the enrollment. Saved {done} of {count}.",
    },
}


@dataclass
class Guide:
    hass: HomeAssistant
    satellite: str
    speaker: str
    name: str
    count: int
    done: int = 0
    misses: int = 0
    answered: bool = False  # a transcript arrived in the current listening turn
    listened: bool = False  # the satellite listened since the last prompt
    replied: bool = False  # the agent answered this turn itself (enrollment_reply)
    final_spoken: bool = False  # the agent already said "done" (enrollment_reply)
    active: bool = True

    def __post_init__(self) -> None:
        language = (self.hass.config.language or "en").split("-")[0]
        self._text = MESSAGES.get(language, MESSAGES["en"])
        self._unsub = async_track_state_change_event(
            self.hass, [self.satellite], self._state_changed
        )
        self._resume: CALLBACK_TYPE | None = None

    def message(self, key: str) -> str:
        return self._text[key].format(name=self.name, done=self.done, count=self.count)

    async def async_start(self) -> None:
        await self._ask(self.message("start"))

    async def _ask(self, text: str) -> None:
        self.answered = False
        self.listened = False
        await self.hass.services.async_call(
            "assist_satellite",
            "start_conversation",
            {"entity_id": self.satellite, "start_message": text, "preannounce": False},
            blocking=False,
        )

    async def _say(self, text: str) -> None:
        await self.hass.services.async_call(
            "assist_satellite",
            "announce",
            {"entity_id": self.satellite, "message": text, "preannounce": False},
            blocking=False,
        )

    @callback
    def on_result(self, result: SpeakerResult) -> None:
        """A transcript of this pipeline run (any satellite - enrollment is global)."""
        if not self.active or result.enrollment is None:
            return
        if result.enrollment.get("speaker") != self.speaker:
            return
        self.answered = True
        done = int(result.enrollment.get("done", 0))
        self.misses = 0 if done > self.done else self.misses + 1
        self.done = done

    @callback
    def agent_replied(self, finished: bool) -> None:
        """The agent said the progress itself and keeps listening (or said "done")."""
        self.replied = True
        self.final_spoken = finished

    @callback
    def _state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Ask only when nobody else will: silence, a conversation that really ended, or
        an agent that does not know about enrollment. Never repeat the agent's progress."""
        new = event.data["new_state"]
        if not self.active or new is None:
            return
        if new.state == "listening":
            self._cancel_resume()
            self.listened, self.answered, self.replied = True, False, False
            return
        # idle after the message, before the satellite listened at all - not an answer yet
        if new.state != "idle" or not self.listened:
            return
        if self.done >= self.count:
            self.stop()
            if not self.final_spoken:
                self.hass.async_create_task(self._say(self.message("done")))
            return
        if self.answered and self.replied:
            # the agent already said „Nagrano X z N” and keeps listening - the satellite is often
            # idle for a moment before listening again; ask only when the conversation broke off
            self._cancel_resume()
            self._resume = async_call_later(self.hass, RESUME_SECONDS, self._resumed)
            return
        if not self.answered:
            self.misses += 1  # silence: the satellite stopped listening without a transcript
        if self.misses >= MAX_MISSES:
            self.stop()
            self.hass.async_create_task(self._say(self.message("stopped")))
            return
        key = "silence" if not self.answered else ("short" if self.misses else "next")
        self.hass.async_create_task(self._ask(self.message(key)))

    @callback
    def _resumed(self, _now) -> None:
        self._resume = None
        state = self.hass.states.get(self.satellite)
        if self.active and state is not None and state.state == "idle":
            self.hass.async_create_task(self._ask(self._text["continue"]))

    @callback
    def _cancel_resume(self) -> None:
        if self._resume is not None:
            self._resume()
            self._resume = None

    @callback
    def stop(self) -> None:
        if self.active:
            self.active = False
            self._unsub()
            self._cancel_resume()
            _LOGGER.info(
                "Guided enrollment of %s ended (%d/%d)", self.speaker, self.done, self.count
            )
