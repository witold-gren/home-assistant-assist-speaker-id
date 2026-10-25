"""HTTP management API (enrollment, calibration, tests) - JSON, optional bearer token.

GET    /api/info                         version, model, settings, enrollment
GET    /api/settings                     shared threshold, margin, min_speech
PUT    /api/settings                     {"threshold": 0.6, "margin": 0.05, "min_speech": 1.0}
GET    /api/speakers                     enrolled speakers (no embeddings)
PUT    /api/speakers/{id}                {"name": ..., "threshold": null|float}
DELETE /api/speakers/{id}                forget a speaker (voiceprint deleted)
POST   /api/speakers/{id}/samples        body: WAV -> one sample
POST   /api/speakers/{id}/samples/last   the last utterance (<= 10 min) -> sample
DELETE /api/speakers/{id}/samples/{n}
POST   /api/enrollment                   {"speaker": id, "count": 10} - next N utterances
DELETE /api/enrollment                   stop enrollment
GET    /api/last                         last utterance: text, result, scores
GET    /api/last.wav                     its audio (RAM only, 10 min)
POST   /api/identify                     body: WAV -> identification
POST   /api/calibrate[?apply=1]          threshold report (and apply it)
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from typing import Any

from aiohttp import web

from . import __version__
from .audio import RATE, from_wav, speech_only
from .service import Enrollment, SpeakerService

_LOGGER = logging.getLogger(__name__)

MAX_WAV_BYTES = 10 * 1024 * 1024


def build_app(service: SpeakerService, token: str | None) -> web.Application:
    @web.middleware
    async def auth(request: web.Request, handler):
        if token:
            given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(given, token):
                raise web.HTTPUnauthorized(text="missing or wrong bearer token")
        try:
            return await handler(request)
        except (ValueError, LookupError) as err:
            raise web.HTTPBadRequest(text=str(err)) from err

    app = web.Application(middlewares=[auth], client_max_size=MAX_WAV_BYTES)
    store = service.store
    routes = web.RouteTableDef()

    def speaker_or_404(request: web.Request) -> str:
        speaker_id = request.match_info["id"]
        if speaker_id not in store.speakers:
            raise web.HTTPNotFound(text=f"unknown speaker {speaker_id}")
        return speaker_id

    async def embedding_of_wav(request: web.Request):
        audio = from_wav(await request.read())
        speech = speech_only(audio, service.settings.max_speech)
        if len(speech) / RATE < store.min_speech:
            raise ValueError(f"not enough speech ({len(speech) / RATE:.1f} s)")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, service.embedder.embed, speech), len(speech) / RATE

    @routes.get("/api/info")
    async def info(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "version": __version__,
                "model": service.embedder.model,
                "dim": service.embedder.dim,
                "threshold": store.threshold,
                "margin": store.margin,
                "min_speech": store.min_speech,
                "settings_from": store.settings_from,
                "speakers": len(store.speakers),
                "enrollment": service.enrollment.to_dict() if service.enrollment else None,
            }
        )

    @routes.get("/api/settings")
    async def get_settings(_: web.Request) -> web.Response:
        return web.json_response({**store.settings(), "from": store.settings_from})

    @routes.put("/api/settings")
    async def put_settings(request: web.Request) -> web.Response:
        body: dict[str, Any] = await request.json()

        def number(key: str) -> float | None:
            return float(body[key]) if body.get(key) is not None else None

        settings = store.set_settings(number("threshold"), number("margin"), number("min_speech"))
        return web.json_response({**settings, "from": store.settings_from})

    @routes.get("/api/speakers")
    async def speakers(_: web.Request) -> web.Response:
        return web.json_response([s.summary() for s in store.speakers.values()])

    @routes.put("/api/speakers/{id}")
    async def upsert(request: web.Request) -> web.Response:
        body: dict[str, Any] = await request.json() if request.can_read_body else {}
        threshold = body.get("threshold")
        speaker = store.upsert(
            request.match_info["id"],
            body.get("name"),
            float(threshold) if threshold is not None else None,
        )
        return web.json_response(speaker.summary())

    @routes.delete("/api/speakers/{id}")
    async def delete(request: web.Request) -> web.Response:
        speaker_id = speaker_or_404(request)
        if service.enrollment and service.enrollment.speaker == speaker_id:
            service.enrollment = None
        store.delete(speaker_id)
        return web.json_response({"deleted": speaker_id})

    @routes.post("/api/speakers/{id}/samples")
    async def add_sample(request: web.Request) -> web.Response:
        speaker_id = speaker_or_404(request)
        embedding, seconds = await embedding_of_wav(request)
        score = store.add_sample(speaker_id, embedding, seconds)
        return web.json_response({**store.speakers[speaker_id].summary(), "score": score})

    @routes.post("/api/speakers/{id}/samples/last")
    async def add_last(request: web.Request) -> web.Response:
        speaker_id = speaker_or_404(request)
        score = service.add_last_as_sample(speaker_id)
        return web.json_response({**store.speakers[speaker_id].summary(), "score": score})

    @routes.delete("/api/speakers/{id}/samples/{n}")
    async def delete_sample(request: web.Request) -> web.Response:
        speaker_id = speaker_or_404(request)
        if not store.delete_sample(speaker_id, int(request.match_info["n"])):
            raise web.HTTPNotFound(text="no such sample")
        return web.json_response(store.speakers[speaker_id].summary())

    @routes.post("/api/enrollment")
    async def start_enrollment(request: web.Request) -> web.Response:
        body = await request.json()
        speaker_id = body["speaker"]
        if speaker_id not in store.speakers:
            store.upsert(speaker_id, body.get("name"), None)
        count = int(body.get("count", 10))
        if not 1 <= count <= 50:
            raise ValueError("count: 1-50")
        service.enrollment = Enrollment(speaker_id, count)
        _LOGGER.info("Enrollment started: next %d utterance(s) -> %s", count, speaker_id)
        return web.json_response(service.enrollment.to_dict())

    @routes.delete("/api/enrollment")
    async def stop_enrollment(_: web.Request) -> web.Response:
        stopped, service.enrollment = service.enrollment, None
        return web.json_response({"stopped": stopped.to_dict() if stopped else None})

    @routes.get("/api/last")
    async def last(_: web.Request) -> web.Response:
        utt = service.last()
        if utt is None:
            raise web.HTTPNotFound(text="no utterance in the last 10 minutes")
        return web.json_response(utt.to_dict())

    @routes.get("/api/last.wav")
    async def last_wav(_: web.Request) -> web.Response:
        wav = service.last_wav()
        if wav is None:
            raise web.HTTPNotFound(text="no utterance in the last 10 minutes")
        return web.Response(body=wav, content_type="audio/wav")

    @routes.post("/api/identify")
    async def identify(request: web.Request) -> web.Response:
        embedding, seconds = await embedding_of_wav(request)
        return web.json_response({**store.identify(embedding).to_dict(), "speech_seconds": seconds})

    @routes.post("/api/calibrate")
    async def calibrate(request: web.Request) -> web.Response:
        report = store.calibrate()
        if request.query.get("apply") in ("1", "true", "yes"):
            store.apply_calibration(report)
            report["applied"] = True
        return web.json_response(report)

    app.add_routes(routes)
    return app
