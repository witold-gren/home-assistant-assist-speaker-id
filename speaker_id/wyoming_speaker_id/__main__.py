"""wyoming-speaker-id: run the Wyoming proxy and the HTTP management API."""

from __future__ import annotations

import argparse
import asyncio
from functools import partial
import json
import logging
import os
from pathlib import Path

from aiohttp import web
from wyoming.server import AsyncServer

from . import __version__, supervisor
from .embedder import DEFAULT_MODEL, Embedder, ensure_model
from .handler import SpeakerIdHandler
from .http_api import build_app
from .service import Settings, SpeakerService
from .speakers import SpeakerStore

_LOGGER = logging.getLogger(__name__)


# Home Assistant add-on: options from the add-on configuration page
ADDON_OPTIONS = Path("/data/options.json")
ADDON_ENV = {
    "upstream": "SPEAKER_ID_UPSTREAM",
    "api_token": "SPEAKER_ID_API_TOKEN",
    "model": "SPEAKER_ID_MODEL",
    "threads": "SPEAKER_ID_THREADS",
    "threshold": "SPEAKER_ID_THRESHOLD",
    "margin": "SPEAKER_ID_MARGIN",
    "min_speech": "SPEAKER_ID_MIN_SPEECH",
    "max_speech": "SPEAKER_ID_MAX_SPEECH",
}


def addon_environment() -> dict[str, str]:
    """Environment overrides from /data/options.json (only inside an add-on)."""
    if not (ADDON_OPTIONS.is_file() and os.environ.get("SUPERVISOR_TOKEN")):
        return {}
    options = json.loads(ADDON_OPTIONS.read_text(encoding="utf-8"))
    found = {
        ADDON_ENV[key]: str(value)
        for key, value in options.items()
        if key in ADDON_ENV and value not in (None, "")
    }
    if options.get("debug"):
        found["SPEAKER_ID_DEBUG"] = "1"
    return found


def parse_args() -> argparse.Namespace:
    overrides = addon_environment()

    def env(key: str, default: str | None = None) -> str | None:
        return overrides.get(key) or os.environ.get(key, default)

    parser = argparse.ArgumentParser(prog="wyoming-speaker-id", description=__doc__)
    parser.add_argument("--uri", default=env("SPEAKER_ID_URI", "tcp://0.0.0.0:10350"))
    parser.add_argument(
        "--upstream",
        default=env("SPEAKER_ID_UPSTREAM"),
        help="Wyoming ASR server to forward to, e.g. tcp://whisper:10300",
    )
    parser.add_argument("--http-host", default=env("SPEAKER_ID_HTTP_HOST", "0.0.0.0"))
    parser.add_argument("--http-port", type=int, default=int(env("SPEAKER_ID_HTTP_PORT", "10351")))
    parser.add_argument("--api-token", default=env("SPEAKER_ID_API_TOKEN"))
    parser.add_argument("--data-dir", type=Path, default=Path(env("SPEAKER_ID_DATA", "/data")))
    parser.add_argument("--model", default=env("SPEAKER_ID_MODEL", DEFAULT_MODEL))
    parser.add_argument("--threads", type=int, default=int(env("SPEAKER_ID_THREADS", "2")))
    parser.add_argument(
        "--threshold", type=float, default=float(env("SPEAKER_ID_THRESHOLD", "0.5"))
    )
    parser.add_argument("--margin", type=float, default=float(env("SPEAKER_ID_MARGIN", "0.05")))
    parser.add_argument(
        "--min-speech", type=float, default=float(env("SPEAKER_ID_MIN_SPEECH", "1.0"))
    )
    parser.add_argument(
        "--max-speech", type=float, default=float(env("SPEAKER_ID_MAX_SPEECH", "8.0"))
    )
    parser.add_argument("--debug", action="store_true", default=env("SPEAKER_ID_DEBUG") == "1")
    args = parser.parse_args()
    if not args.upstream:
        parser.error("--upstream (or SPEAKER_ID_UPSTREAM) is required")
    return args


async def run(args: argparse.Namespace) -> None:
    # model baked into the image (/models) or downloaded once into the data directory
    built_in = Path(os.environ.get("SPEAKER_ID_MODELS", "/models")) / Path(args.model).name
    model = built_in if built_in.is_file() else ensure_model(args.model, args.data_dir / "models")
    embedder = Embedder(model, args.threads)
    store = SpeakerStore(
        args.data_dir / "speakers.json", args.threshold, args.margin, args.min_speech
    )
    settings = Settings(args.upstream, min_speech=args.min_speech, max_speech=args.max_speech)
    service = SpeakerService(settings, store, embedder)

    token = args.api_token
    if supervisor.in_addon() and not token:
        token = supervisor.addon_api_token(args.data_dir)
    runner = web.AppRunner(build_app(service, token), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, args.http_host, args.http_port).start()
    if supervisor.in_addon():
        port = int(args.uri.rsplit(":", 1)[-1])
        await supervisor.announce(port, args.http_port, token)
    elif not token:
        _LOGGER.warning(
            "HTTP API without a token - keep port %d inside your network", args.http_port
        )

    _LOGGER.info(
        "wyoming-speaker-id %s: %s -> %s, model %s (%d dims), %d speaker(s), HTTP :%d",
        __version__,
        args.uri,
        args.upstream,
        embedder.model,
        embedder.dim,
        len(store.speakers),
        args.http_port,
    )
    server = AsyncServer.from_uri(args.uri)
    try:
        await server.run(partial(SpeakerIdHandler, service=service))
    finally:
        await runner.cleanup()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
