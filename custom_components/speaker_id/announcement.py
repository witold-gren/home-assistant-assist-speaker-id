"""Intercom: play the speaker's own recording (the last utterance) on other speakers.

„Ogłoszenie, dzieci proszę przyjść na obiad” - instead of text-to-speech the satellites
play exactly what was said, unchanged. The recording comes from wyoming-speaker-id (the
last utterance, RAM only) and is served by Home Assistant under a one-time token, for a
few minutes, behind a signed URL - nothing is written to disk.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import secrets
import time
from typing import Any

from aiohttp import web
from homeassistant.components.assist_satellite import AssistSatelliteEntityFeature
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.media_player import async_process_play_media_url
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util.hass_dict import HassKey

from .client import SpeakerIdClient

_LOGGER = logging.getLogger(__name__)

URL = "/api/speaker_id/announcement/{token}"
KEEP_SECONDS = 600  # how long the recording stays available (satellites fetch it right away)
MAX_AGE = 30.0  # an older last utterance is not this announcement
# a player in these states will not play; "off" is how Apple TV shows a disconnected HomePod
# (a HomePod has no power switch, and play_media on a disconnected device silently does nothing)
PLAYER_DOWN = ("unavailable", "unknown", "off")

DATA: HassKey[dict[str, tuple[bytes, float]]] = HassKey("speaker_id_announcements")


@dataclass
class Announced:
    satellites: list[str]
    media_players: list[str]


class AnnouncementView(HomeAssistantView):
    """WAV of an announcement; the signed URL HA creates for the players authorises it."""

    url = URL
    name = "api:speaker_id:announcement"
    requires_auth = True

    async def get(self, request: web.Request, token: str) -> web.Response:
        store = request.app["hass"].data.get(DATA, {})
        entry = store.get(token)
        if entry is None or entry[1] < time.monotonic():
            raise web.HTTPNotFound
        return web.Response(body=entry[0], content_type="audio/wav")


def async_setup_announcements(hass: HomeAssistant) -> None:
    hass.data.setdefault(DATA, {})
    hass.http.register_view(AnnouncementView())


def _store(hass: HomeAssistant, wav: bytes) -> str:
    store = hass.data.setdefault(DATA, {})
    now = time.monotonic()
    for token in [t for t, (_, until) in store.items() if until < now]:
        del store[token]
    token = secrets.token_urlsafe(16)
    store[token] = (wav, now + KEEP_SECONDS)
    return URL.format(token=token)


def _area(hass: HomeAssistant, entity_id: str) -> str | None:
    """Area of an entity - its own, otherwise its device's."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return None
    if entry.area_id:
        return entry.area_id
    device = dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
    return device.area_id if device else None


def players_first(
    hass: HomeAssistant, satellites: list[str], players: list[str], exclude: str | None
) -> tuple[list[str], list[str]]:
    """One speaker per room: a media player (e.g. a HomePod) when it is available,
    otherwise the room's satellite. The room the announcement comes from gets nothing."""
    here = _area(hass, exclude) if exclude else None
    up = [
        p
        for p in players
        if (state := hass.states.get(p)) is not None and state.state not in PLAYER_DOWN
    ]
    up = [p for p in up if here is None or _area(hass, p) != here]
    covered = {area for p in up if (area := _area(hass, p))}
    return [s for s in satellites if _area(hass, s) not in covered], up


def announce_satellites(hass: HomeAssistant, exclude: str | None) -> list[str]:
    """All available voice satellites that can announce, except the one that recorded."""
    return [
        state.entity_id
        for state in hass.states.async_all("assist_satellite")
        if state.entity_id != exclude
        and state.state != "unavailable"
        and state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) & AssistSatelliteEntityFeature.ANNOUNCE
    ]


async def async_announce(
    hass: HomeAssistant,
    client: SpeakerIdClient,
    *,
    exclude: str | None = None,
    satellites: list[str] | None = None,
    media_players: list[str] | None = None,
    preannounce: bool = True,
    max_age: float = MAX_AGE,
    prefer_players: bool = False,
) -> Announced:
    """Play the last utterance; LookupError when there is no fresh recording.

    ``prefer_players`` - in a room with an available media player (HomePod) only the
    player plays, the satellite there stays silent; see players_first().
    """
    wav, age = await client.last_audio()
    if age > max_age:
        raise LookupError(f"the last utterance is {age:.0f} s old")
    path = _store(hass, wav)
    targets = announce_satellites(hass, exclude) if satellites is None else satellites
    players = [p for p in media_players or [] if p != exclude]
    if prefer_players:
        targets, players = players_first(hass, targets, players, exclude)
    if targets:
        await hass.services.async_call(
            "assist_satellite",
            "announce",
            {"media_id": path, "preannounce": preannounce},
            target={"entity_id": targets},
            blocking=False,
        )
    if players:
        # media players (e.g. HomePods through Music Assistant) - full, signed URL
        url = async_process_play_media_url(hass, path)
        data: dict[str, Any] = {
            "media_content_id": url,
            "media_content_type": "music",
            "announce": True,
        }
        await hass.services.async_call(
            "media_player", "play_media", data, target={"entity_id": players}, blocking=False
        )
    _LOGGER.info("Announcement (%d bytes) to %s", len(wav), targets + players)
    return Announced(targets, players)
