"""Recording a voiceprint and calibrating from the device, without writing actions.

The device „Assist Speaker ID” has a person, a satellite and the number of samples to choose, and
the buttons Start / Stop recording and Calibrate. The same work as the actions
`speaker_id.start_enrollment` (with `satellite`), `stop_enrollment` and `calibrate`, which stay.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .client import SpeakerIdError
from .const import CONF_PEOPLE, DOMAIN, SIGNAL_CALIBRATION, SIGNAL_ENROLLMENT
from .guide import Guide

if TYPE_CHECKING:
    from . import SpeakerIdConfigEntry

NOTIFICATION_ID = f"{DOMAIN}_calibration"

TEXTS = {
    "pl": {
        "title": "Assist Speaker ID: kalibracja",
        "head": "| Osoba | Próbki | Najgorsza własna | Najbliższy obcy | Rozdzielne "
        "| Sugerowany próg |",
        "yes": "tak",
        "no": "**nie**",
        "few": "za mało próbek",
        "all_separable": "Wszystkie głosy są rozdzielne: żadna obca próbka nie zbliżyła się do "
        "najgorszej własnej.",
        "overlap": "Niektóre głosy się nakładają ({names}). Nagraj im więcej próbek "
        "(Rozpocznij nagrywanie) i skalibruj jeszcze raz.",
        "hint": "Próg ustawisz w konfiguracji dodatku: Ustawienia → Urządzenia i usługi → "
        "Assist Speaker ID → Konfiguruj. Margines: {margin}.",
        "empty": "Brak nagranych głosów. Wybierz osobę i satelitę, potem Rozpocznij nagrywanie.",
    },
    "en": {
        "title": "Assist Speaker ID: calibration",
        "head": "| Person | Samples | Worst own | Closest other | Separable "
        "| Suggested threshold |",
        "yes": "yes",
        "no": "**no**",
        "few": "too few samples",
        "all_separable": "All voices are separable: no other person's sample came close to the "
        "worst own one.",
        "overlap": "Some voices overlap ({names}). Record more samples for them (Start recording) "
        "and calibrate again.",
        "hint": "Set the threshold in the integration's options: Settings → Devices & services → "
        "Assist Speaker ID → Configure. Margin: {margin}.",
        "empty": "No voices recorded yet. Choose a person and a satellite, then Start recording.",
    },
    "fr": {
        "title": "Assist Speaker ID : calibrage",
        "head": "| Personne | Échantillons | Pire propre | Plus proche étranger | Séparables "
        "| Seuil suggéré |",
        "yes": "oui",
        "no": "**non**",
        "few": "Pas assez d'échantillons",
        "all_separable": "Toutes les voix sont distinguables.",
        "overlap": "Certaines voix sont trop proches ({names}), enregistrez plus d'échantillons.",
        "hint": "Réglez le seuil dans les options de l'intégration : "
        "Paramètres → Appareils et services → Assist Speaker ID → Configurer. Marge : {margin}.",
        "empty": "Aucune voix enregistrée. Choisissez une personne et un satellite, puis Démarrer "
        "l'enregistrement.",
    },
}


def _texts(hass: HomeAssistant) -> dict[str, str]:
    return TEXTS.get((hass.config.language or "en").split("-")[0], TEXTS["en"])


def speaker_id_of(object_id: str) -> str:
    """A person's object id as a speaker id (lowercase letters, digits and _, up to 32)."""
    speaker = re.sub(r"[^a-z0-9_]+", "_", object_id.lower()).strip("_")[:32]
    return speaker or "person"


def speaker_for_person(
    hass: HomeAssistant, entry: SpeakerIdConfigEntry, person: str
) -> tuple[str, str]:
    """(speaker id, name): the voice already mapped to the person, or one named after them."""
    people: dict[str, str] = entry.options.get(CONF_PEOPLE, {})
    speaker = next((sid for sid, pid in people.items() if pid == person), None)
    state = hass.states.get(person)
    name = state.name if state else person.split(".", 1)[1]
    return speaker or speaker_id_of(person.split(".", 1)[1]), name


@callback
def async_enrollment_changed(hass: HomeAssistant, entry: SpeakerIdConfigEntry) -> None:
    async_dispatcher_send(hass, SIGNAL_ENROLLMENT.format(entry_id=entry.entry_id))


async def _api(entry: SpeakerIdConfigEntry, method: str, path: str, json: Any = None) -> Any:
    try:
        return await entry.runtime_data.client.api(method, path, json)
    except SpeakerIdError as err:
        raise HomeAssistantError(str(err)) from err


async def async_start_enrollment(
    hass: HomeAssistant,
    entry: SpeakerIdConfigEntry,
    speaker: str,
    name: str | None,
    count: int,
    satellite: str | None,
) -> Any:
    """The next `count` utterances become the speaker's samples; with a satellite it guides."""
    body: dict[str, Any] = {"speaker": speaker, "count": count}
    if name:
        body["name"] = name
    response = await _api(entry, "POST", "enrollment", body)
    runtime = entry.runtime_data
    runtime.enrolled = 0
    if runtime.guide is not None:
        runtime.guide.stop()
        runtime.guide = None
    if satellite:
        start = int(response.get("total", 0)) if isinstance(response, dict) else 0
        runtime.guide = Guide(
            hass,
            satellite,
            speaker,
            name or speaker,
            count,
            start=start,
            on_change=lambda: async_enrollment_changed(hass, entry),
        )
        await runtime.guide.async_start()
    async_enrollment_changed(hass, entry)
    return response


async def async_stop_enrollment(hass: HomeAssistant, entry: SpeakerIdConfigEntry) -> Any:
    runtime = entry.runtime_data
    if runtime.guide is not None:
        runtime.guide.stop()
        runtime.guide = None
    response = await _api(entry, "DELETE", "enrollment")
    async_enrollment_changed(hass, entry)
    return response


async def async_start_from_device(hass: HomeAssistant, entry: SpeakerIdConfigEntry) -> Any:
    """Start recording the chosen person on the chosen satellite (the device's button)."""
    runtime = entry.runtime_data
    if not runtime.person or hass.states.get(runtime.person) is None:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="choose_person")
    if not runtime.satellite or hass.states.get(runtime.satellite) is None:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="choose_satellite")
    speaker, name = speaker_for_person(hass, entry, runtime.person)
    people: dict[str, str] = entry.options.get(CONF_PEOPLE, {})
    if people.get(speaker) != runtime.person:
        # the voice belongs to this person from now on (also its HA user) - no reload for that
        runtime.skip_reload = True
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_PEOPLE: {**people, speaker: runtime.person}}
        )
    return await async_start_enrollment(
        hass, entry, speaker, name, runtime.samples, runtime.satellite
    )


def _names(hass: HomeAssistant, entry: SpeakerIdConfigEntry, speakers: list[dict]) -> dict:
    """speaker id -> the name to show: the mapped person's, the saved one, or the id."""
    people: dict[str, str] = entry.options.get(CONF_PEOPLE, {})
    names = {s["id"]: s.get("name") or s["id"] for s in speakers if isinstance(s, dict)}
    for speaker, person in people.items():
        if state := hass.states.get(person):
            names[speaker] = state.name
    return names


def calibration_message(hass: HomeAssistant, report: dict[str, Any], names: dict) -> str:
    """The report as a short table for a notification."""
    text = _texts(hass)
    speakers: dict[str, dict] = report.get("speakers", {})
    if not speakers:
        return text["empty"]
    rows = [text["head"], "|---|---|---|---|---|---|"]
    overlapping = []
    for speaker, row in speakers.items():
        name = names.get(speaker, speaker)
        if "own_min" not in row:
            rows.append(f"| {name} | {row.get('samples', 0)} | {text['few']} | | | |")
            continue
        separable = bool(row.get("separable"))
        if not separable:
            overlapping.append(name)
        rows.append(
            f"| {name} | {row['samples']} | {row['own_min']:.2f} | {row['others_max']:.2f} "
            f"| {text['yes'] if separable else text['no']} | {row['suggested_threshold']:.2f} |"
        )
    summary = (
        text["overlap"].format(names=", ".join(overlapping))
        if overlapping
        else text["all_separable"]
    )
    margin = report.get("margin")
    hint = text["hint"].format(margin=f"{margin:.2f}" if isinstance(margin, (int, float)) else "-")
    return "\n".join(rows) + f"\n\n{summary}\n\n{hint}"


async def async_calibrate_from_device(
    hass: HomeAssistant, entry: SpeakerIdConfigEntry
) -> dict[str, Any]:
    """Calibrate (nothing is changed) and show the result: a notification and the sensor."""
    report = await _api(entry, "POST", "calibrate")
    speakers = await _api(entry, "GET", "speakers")
    names = _names(hass, entry, speakers if isinstance(speakers, list) else [])
    entry.runtime_data.calibration = {"report": report, "names": names}
    persistent_notification.async_create(
        hass,
        calibration_message(hass, report, names),
        title=_texts(hass)["title"],
        notification_id=NOTIFICATION_ID,
    )
    async_dispatcher_send(hass, SIGNAL_CALIBRATION.format(entry_id=entry.entry_id))
    return report
