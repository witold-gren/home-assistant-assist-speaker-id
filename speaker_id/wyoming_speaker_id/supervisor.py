"""Home Assistant add-on: API token and Supervisor discovery of the service.

Inside an add-on (SUPERVISOR_TOKEN set) the service announces itself to Home
Assistant, so the Assist Speaker ID integration is offered without typing host, ports
and token. Outside an add-on nothing here runs.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import secrets
import socket

import aiohttp

_LOGGER = logging.getLogger(__name__)

SUPERVISOR = "http://supervisor"
DISCOVERY_SERVICE = "speaker_id"


def in_addon() -> bool:
    return bool(os.environ.get("SUPERVISOR_TOKEN"))


def addon_api_token(data_dir: Path) -> str:
    """A random API token kept in the add-on's /data (created on first start)."""
    path = data_dir / "api_token"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    path.chmod(0o600)
    return token


async def announce(port: int, api_port: int, token: str) -> None:
    """Tell the Supervisor how Home Assistant reaches this add-on."""
    config = {
        "host": socket.gethostname(),  # the add-on's hostname inside the HA network
        "port": port,
        "api_port": api_port,
        "api_token": token,
    }
    headers = {"Authorization": f"Bearer {os.environ['SUPERVISOR_TOKEN']}"}
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                f"{SUPERVISOR}/discovery",
                json={"service": DISCOVERY_SERVICE, "config": config},
                headers=headers,
                timeout=aiohttp.ClientTimeout(10),
            ) as resp,
        ):
            if resp.status >= 400:
                _LOGGER.warning("Supervisor discovery failed: HTTP %s", resp.status)
            else:
                _LOGGER.info("Announced to Home Assistant as %s", config["host"])
    except (aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.warning("Supervisor discovery failed: %s", err)
