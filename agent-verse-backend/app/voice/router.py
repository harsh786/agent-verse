"""Voice OS router — STT, TTS, streaming, greeting, persona.

Endpoints:
  GET  /v1/voice/status                    — provider readiness
  POST /v1/voice/transcribe               — audio → transcript (native STT)
  POST /v1/voice/speak                    — text → WAV audio (native TTS)
  GET  /v1/voice/greeting/{org_id}        — spoken login greeting with real org stats
  POST /v1/voice/persona/{org_id}         — upload org voice persona (D-5 cloning)
  DEL  /v1/voice/persona/{org_id}         — delete org voice persona
  WS   /v1/voice/stream/{org_id}          — real-time bidirectional voice session

Differentiators:
  D-1: Voice-to-Mission; D-2: WYWA digest; D-3: Intent Router;
  D-4: Voice-driven approval; D-5: Multi-language; D-7: Real org health
"""

from __future__ import annotations

import io
import json
import os
from typing import Any
from uuid import uuid4

import structlog
from fastapi import (
    APIRouter,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    status,
)
from fastapi.responses import StreamingResponse
from opentelemetry import trace

from app.voice.greeting import jurisdiction_to_language, synthesize_greeting
from app.voice.schemas import PersonaResponse, SpeakRequest, TranscribeResponse, VoiceStatusResponse
from app.voice.streaming import VoiceStreamingSession
from app.voice.stt_engine import transcribe
from app.voice.tts_engine import SAMPLE_RATE, synthesize

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)
router = APIRouter(prefix="/v1/voice", tags=["voice"])


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "type": "unauthorized",
                "title": "Unauthorized",
                "status": 401,
                "detail": "Missing or invalid API key",
            },
        )
    return ctx


def _tenant_id(ctx: Any) -> str:
    return str(getattr(ctx, "tenant_id", None) or getattr(ctx, "id", ctx))


def _request_id() -> str:
    return str(uuid4())


def _resolved_speech(capability: str) -> tuple[str, str, str, str | None]:
    """``(model, source, provider, error)`` the resolver picks for *capability*."""
    from app.ai_router.resolve import ModelNotConfiguredError, resolve_stt, resolve_tts

    try:
        res = resolve_stt() if capability == "speech_to_text" else resolve_tts()
    except ModelNotConfiguredError as exc:
        if capability == "text_to_speech":
            return "", "degraded", "browser", str(exc)
        return "", "not_configured", "", str(exc)
    return res.model, res.source, res.provider, None


@router.get("/status", operation_id="voice_status", response_model=VoiceStatusResponse)
async def voice_status(request: Request) -> VoiceStatusResponse:
    """Provider readiness and the speech models that actually run (and why).

    ``stt_model`` / ``tts_model`` are the resolved models (Model Registry →
    env pins → local engines), not a configured default that may never load.
    """
    _require_tenant(request)
    from app.ai_router.speech import voice_setting

    device = voice_setting("voice_device") or "cpu"
    try:
        from app.voice.providers import get_capabilities

        caps = await get_capabilities()
        stt, tts = caps["stt"], caps["tts"]
        stt_model, stt_source = stt.get("model") or "", stt.get("source") or ""
        tts_model, tts_source = tts.get("model") or "", tts.get("source") or ""
        if not stt_source:
            stt_model, stt_source, _p, _e = _resolved_speech("speech_to_text")
        if not tts_source:
            tts_model, tts_source, _p, _e = _resolved_speech("text_to_speech")
        return VoiceStatusResponse(
            stt_status=("ready" if stt["ready"] else "idle"),
            tts_status=("ready" if tts["ready"] else "idle"),
            stt_provider=stt["provider"],
            tts_provider=tts["provider"],
            stt_model=stt_model,
            tts_model=tts_model,
            stt_source=stt_source,
            tts_source=tts_source,
            device=device,
        )
    except Exception:
        from app.voice import stt_engine, tts_engine

        stt_model, stt_source, stt_provider, stt_error = _resolved_speech("speech_to_text")
        tts_model, tts_source, tts_provider, tts_error = _resolved_speech("text_to_speech")
        return VoiceStatusResponse(
            stt_status=("error" if stt_error else "ready" if stt_engine._model else "idle"),
            tts_status="ready" if tts_engine._model else "idle",
            stt_model=stt_model,
            tts_model=tts_model,
            stt_source=stt_source,
            tts_source=tts_source,
            stt_provider=stt_provider,
            tts_provider=tts_provider,
            stt_error=stt_error,
            tts_error=tts_error,
            device=device,
        )


@router.post(
    "/transcribe",
    operation_id="voice_transcribe",
    response_model=TranscribeResponse,
    status_code=200,
)
async def voice_transcribe(
    request: Request,
    audio: UploadFile = File(description="WAV/WebM/OGG/MP4 <= 25 MB"),
    x_request_id: str = Header(default_factory=_request_id),
) -> TranscribeResponse:
    with tracer.start_as_current_span("voice.api.transcribe") as span:
        ctx = _require_tenant(request)
        tenant_id = _tenant_id(ctx)
        span.set_attribute("tenant_id", tenant_id)
        content = await audio.read()
        max_mb = int(os.getenv("VOICE_MAX_AUDIO_MB", "25"))
        if len(content) > max_mb * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail={
                    "type": "file-too-large",
                    "status": 413,
                    "detail": f"Audio must be <= {max_mb} MB",
                    "request_id": x_request_id,
                },
            )
        try:
            result = await transcribe(content, audio.content_type or "audio/wav")
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "type": "stt-error",
                    "status": 502,
                    "detail": str(exc),
                    "request_id": x_request_id,
                },
            ) from exc
        log.info("voice.transcribe.ok", tenant_id=tenant_id, chars=len(result["transcript"]))
        return TranscribeResponse(**result)


@router.post("/speak", operation_id="voice_speak", status_code=200)
async def voice_speak(
    body: SpeakRequest,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
) -> StreamingResponse:
    with tracer.start_as_current_span("voice.api.speak") as span:
        ctx = _require_tenant(request)
        tenant_id = _tenant_id(ctx)
        span.set_attribute("text_len", len(body.text))
        ref_audio, ref_text = None, None
        if body.use_org_persona and body.org_id:
            ref_audio, ref_text = await _get_persona(request.app, tenant_id, body.org_id)
        try:
            wav = await synthesize(
                body.text,
                ref_audio=ref_audio,
                ref_text=ref_text,
                language=body.language,
                speed=body.speed,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "type": "tts-error",
                    "status": 502,
                    "detail": str(exc),
                    "request_id": x_request_id,
                },
            ) from exc
        return _wav_response(wav, x_request_id)


@router.get("/greeting/{org_id}", operation_id="voice_greeting", status_code=200)
async def voice_greeting(
    org_id: str,
    request: Request,
    user_name: str = Query(default="there", max_length=120),
    language: str = Query(default="", max_length=10),
    x_request_id: str = Header(default_factory=_request_id),
) -> StreamingResponse:
    with tracer.start_as_current_span("voice.api.greeting") as span:
        ctx = _require_tenant(request)
        tenant_id = _tenant_id(ctx)
        span.set_attribute("org_id", org_id)

        # D-5: Auto-detect language from org jurisdiction
        eff_lang = language or "en"
        if not language:
            try:
                sf = getattr(request.app.state, "db_session_factory", None)
                if sf:
                    from app.db.rls import sqlalchemy_rls_context
                    from app.org.service import OrgService

                    async with (
                        sf() as session,
                        session.begin(),
                        sqlalchemy_rls_context(session, tenant_id),
                    ):
                        org = await OrgService(
                            session=session, tenant_id=tenant_id
                        ).get_organization(org_id)
                        if org:
                            eff_lang = jurisdiction_to_language(getattr(org, "jurisdiction", None))
            except Exception:
                pass

        cache_key = f"voice:greeting:{tenant_id}:{org_id}:{eff_lang}"
        cached = await _redis_get(request.app, cache_key)
        if cached:
            return _wav_response(cached, x_request_id)

        health = await _fetch_org_health(request.app, org_id, tenant_id)
        wywa_items, wywa_summary = await _fetch_wywa(request.app, org_id, tenant_id)
        ref_audio, ref_text = await _get_persona(request.app, tenant_id, org_id)

        try:
            wav = await synthesize_greeting(
                health,
                user_name,
                ref_audio=ref_audio,
                ref_text=ref_text,
                language=eff_lang,
                wywa_items=wywa_items,
                wywa_summary=wywa_summary,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "type": "tts-error",
                    "status": 502,
                    "detail": str(exc),
                    "request_id": x_request_id,
                },
            ) from exc

        ttl = int(os.getenv("VOICE_GREETING_CACHE_TTL", "300"))
        await _redis_set(request.app, cache_key, wav, ttl)
        span.set_attribute("wav_bytes", len(wav))
        return _wav_response(wav, x_request_id)


@router.post(
    "/persona/{org_id}", operation_id="voice_persona_upload", response_model=PersonaResponse
)
async def voice_persona_upload(
    org_id: str,
    request: Request,
    audio: UploadFile = File(description="WAV reference audio 3-30s"),
    ref_text: str = Query(description="Transcript of the reference audio"),
    language: str = Query(default="en"),
    x_request_id: str = Header(default_factory=_request_id),
) -> PersonaResponse:
    ctx = _require_tenant(request)
    tenant_id = _tenant_id(ctx)
    content = await audio.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail={
                "type": "file-too-large",
                "status": 413,
                "detail": "Reference audio must be <= 5 MB",
            },
        )
    # The persona lives in Redis (that is what _get_persona reads). This used to
    # swallow a missing/failed Redis and answer 200 with nothing stored.
    if _voice_redis(request.app) is None:
        raise HTTPException(
            status_code=503,
            detail={
                "type": "persona-store-unavailable",
                "status": 503,
                "detail": "Voice persona storage (Redis) is not configured",
            },
        )
    try:
        await _cache_persona(request.app, tenant_id, org_id, content, ref_text, language)
    except Exception as exc:
        log.error("voice.persona_store_failed", error=str(exc)[:200])
        raise HTTPException(
            status_code=503,
            detail={
                "type": "persona-store-unavailable",
                "status": 503,
                "detail": "Voice persona could not be stored; retry",
            },
        ) from exc
    url = await _store_persona_audio(tenant_id, org_id, content)
    import datetime

    return PersonaResponse(
        org_id=org_id,
        tenant_id=tenant_id,
        ref_audio_url=url,
        ref_text=ref_text,
        language=language,
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )


@router.delete(
    "/persona/{org_id}", operation_id="voice_persona_delete", status_code=status.HTTP_204_NO_CONTENT
)
async def voice_persona_delete(org_id: str, request: Request) -> None:
    ctx = _require_tenant(request)
    if _voice_redis(request.app) is None:
        raise HTTPException(status_code=503, detail="Voice persona storage is not configured")
    try:
        await _delete_persona(request.app, _tenant_id(ctx), org_id)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Voice persona could not be deleted; retry"
        ) from exc


@router.websocket("/stream/{org_id}")
async def voice_stream(
    ws: WebSocket,
    org_id: str,
    consent: bool = Query(default=False),
) -> None:
    """D-1/D-3/D-4: Real-time voice session — speak goals, approve missions.

    Auth: the API key travels in the ``X-API-Key`` / ``Authorization`` header or,
    from a browser (which cannot set WebSocket headers), as an
    ``av.v1.<base64url(key)>`` entry in ``Sec-WebSocket-Protocol``.

    D-24: audio processing requires recorded consent. The client either passes
    ``?consent=true`` at connect time or sends a ``{"type":"consent"}`` control
    message; without it the session fails closed and refuses to transcribe.
    """
    # The key used to be read from ``?api_key=`` — so every voice session wrote a
    # long-lived tenant credential into proxy / load-balancer access logs and
    # browser history. The query key is no longer honoured at all (keeping it
    # "for compatibility" would keep the leak). Authenticate BEFORE accept() so
    # the offered av.v1 subprotocol can be echoed (browsers abort otherwise).
    tenant_id = await _ws_auth(ws)
    if not tenant_id:
        await ws.accept()
        await ws.close(code=4001, reason="Unauthorized")
        return
    offered = [
        p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()
    ]
    await ws.accept(subprotocol=next((p for p in offered if p.startswith("av.v1.")), None))

    # D-5: Auto-detect language
    language = "en"
    try:
        sf = getattr(ws.app.state, "db_session_factory", None)  # type: ignore[attr-defined]
        if sf:
            from app.db.rls import sqlalchemy_rls_context
            from app.org.service import OrgService

            async with (
                sf() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                org = await OrgService(session=session, tenant_id=tenant_id).get_organization(
                    org_id
                )
                if org:
                    language = jurisdiction_to_language(getattr(org, "jurisdiction", None))
    except Exception:
        pass

    sf = getattr(ws.app.state, "db_session_factory", None)  # type: ignore[attr-defined]
    ref_audio, ref_text = await _get_persona(ws.app, tenant_id, org_id)  # type: ignore[attr-defined]
    sess = VoiceStreamingSession(
        ws,
        tenant_id=tenant_id,
        org_id=org_id,
        session_factory=sf,
        ref_audio=ref_audio,
        ref_text=ref_text,
        language=language,
        consent_granted=consent,
        speaker_id=f"{tenant_id}:{org_id}",
    )
    await sess.run()


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _fetch_org_health(app: Any, org_id: str, tenant_id: str) -> dict:
    sf = getattr(getattr(app, "state", None), "db_session_factory", None)
    if not sf:
        return _fallback_health(org_id)
    try:
        from app.db.rls import sqlalchemy_rls_context
        from app.org.service import OrgService

        async with (
            sf() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            return await OrgService(session=session, tenant_id=tenant_id).get_org_health(org_id)
    except Exception as exc:
        log.warning("voice.health_fetch_failed", error=str(exc))
        return _fallback_health(org_id)


def _fallback_health(org_id: str) -> dict:
    return {
        "org_id": org_id,
        "org_name": "your organisation",
        # Unknown, not "healthy": this is the fallback when the real org health
        # could not be read.
        "overall_health": "unknown",
        "active_missions": 0,
        "active_teams": 0,
        "pending_approvals": 0,
        "items_needing_attention": 0,
    }


async def _fetch_wywa(app: Any, org_id: str, tenant_id: str) -> tuple[int, str]:
    sf = getattr(getattr(app, "state", None), "db_session_factory", None)
    redis = getattr(getattr(app, "state", None), "redis", None)
    if not sf:
        return 0, ""
    try:
        from app.db.rls import sqlalchemy_rls_context
        from app.org.digest import DigestGenerator

        async with sf() as session, sqlalchemy_rls_context(session, tenant_id):
            digest = await DigestGenerator(session=session, redis=redis).generate(
                org_id, tenant_id
            )
            count = len(getattr(digest, "missions_completed", [])) + len(
                getattr(digest, "pending_approvals", [])
            )
            return count, (f"{count} updates while you were away." if count else "")
    except Exception as exc:
        log.debug("voice.wywa_failed", error=str(exc))
        return 0, ""


def _voice_redis(app: Any) -> Any:
    """Binary-safe Redis for voice caches: an explicitly injected
    ``app.state.redis`` (tests / embedders; never set by create_app), else the
    lifespan's ``app.state.voice_redis``."""
    state = getattr(app, "state", None)
    return getattr(state, "redis", None) or getattr(state, "voice_redis", None)


async def _get_persona(app: Any, tid: str, org_id: str) -> tuple[bytes | None, str | None]:
    try:
        redis = _voice_redis(app)
        if not redis:
            return None, None
        import base64

        data = await redis.hgetall(f"voice:persona:{tid}:{org_id}")
        if not data:
            return None, None
        audio = base64.b64decode(data[b"audio"]) if b"audio" in data else None
        text = data.get(b"text", b"").decode()
        return audio, text or None
    except Exception:
        return None, None


async def _cache_persona(
    app: Any, tid: str, org_id: str, audio: bytes, ref_text: str, lang: str
) -> None:
    """Store the persona. Raises on a Redis failure (callers must not report a
    persona that was not stored); a no-op only when no Redis is wired."""
    redis = _voice_redis(app)
    if not redis:
        return
    import base64

    await redis.hset(
        f"voice:persona:{tid}:{org_id}",
        mapping={
            "audio": base64.b64encode(audio).decode(),
            "text": ref_text,
            "language": lang,
        },
    )


async def _delete_persona(app: Any, tid: str, org_id: str) -> None:
    """Delete the persona. Raises on a Redis failure (never a fake 204)."""
    redis = _voice_redis(app)
    if redis:
        await redis.delete(f"voice:persona:{tid}:{org_id}")


async def _redis_get(app: Any, key: str) -> bytes | None:
    try:
        redis = _voice_redis(app)
        return await redis.get(key) if redis else None
    except Exception:
        return None


async def _redis_set(app: Any, key: str, value: bytes, ttl: int) -> None:
    try:
        redis = _voice_redis(app)
        if redis:
            await redis.setex(key, ttl, value)
    except Exception:
        pass


async def _store_persona_audio(tid: str, org_id: str, audio: bytes) -> str | None:
    """Archive the reference audio in S3; ``None`` when that fails. It used to
    return a fabricated ``local://`` URL for a file that was written nowhere."""
    try:
        import boto3

        s3 = boto3.client(
            "s3",
            endpoint_url=os.getenv("S3_ENDPOINT_URL"),
            aws_access_key_id=os.getenv("S3_ACCESS_KEY"),
            aws_secret_access_key=os.getenv("S3_SECRET_KEY"),
        )
        bucket = os.getenv("VOICE_PERSONA_BUCKET", "agentverse-voice-personas")
        key = f"{tid}/{org_id}/ref.wav"
        s3.put_object(Bucket=bucket, Key=key, Body=audio, ContentType="audio/wav")
        return f"s3://{bucket}/{key}"
    except Exception as exc:
        log.warning("voice.persona_s3_archive_failed", error=str(exc)[:200])
        return None


async def _ws_auth(ws: WebSocket) -> str | None:
    """Authenticate the WebSocket from headers / the av.v1 subprotocol only.

    Delegates to :func:`app.tenancy.ws_auth.resolve_ws_tenant` with
    headers / subprotocol credentials only. This used to (a) read the key from the URL query
    string and (b) when no ``_tenant_key_resolver`` was wired, return the raw key
    AS the tenant id — any string authenticated as "a tenant".
    """
    from app.tenancy.ws_auth import resolve_ws_tenant

    tenant_ctx = await resolve_ws_tenant(ws, write=True)
    if tenant_ctx is None:
        return None
    tenant_id = getattr(tenant_ctx, "tenant_id", None) or getattr(tenant_ctx, "id", None)
    return str(tenant_id) if tenant_id else None


def _wav_response(wav: bytes, rid: str) -> StreamingResponse:
    return StreamingResponse(
        io.BytesIO(wav),
        media_type="audio/wav",
        headers={
            "Content-Length": str(len(wav)),
            "Content-Disposition": "inline; filename=speech.wav",
            "X-Sample-Rate": str(SAMPLE_RATE),
            "X-Request-Id": rid,
            "Cache-Control": "no-cache",
        },
    )


# ── GET /v1/voice/alerts/stream (D-6 Proactive Voice Alerts via SSE) ─────────


@router.get(
    "/alerts/stream",
    operation_id="voice_alerts_stream",
    summary="SSE stream of proactive TTS alerts (D-6) — mission failures, urgent approvals",
)
async def voice_alerts_stream(request: Request) -> StreamingResponse:
    """D-6: Server-Sent Events stream of base64 PCM audio for proactive alerts.

    Client subscribes once; receives JSON events:
      {"event_type": "mission_failed", "text": "...", "chunks": ["<base64>", ...]}
    """
    ctx = _require_tenant(request)
    tenant_id = _tenant_id(ctx)

    from app.voice.alerts import VoiceAlertManager

    alert_mgr: VoiceAlertManager | None = getattr(request.app.state, "voice_alert_manager", None)
    if alert_mgr is None:
        # Lazy-create the manager if not in app.state (e.g. dev mode without full lifespan)
        redis = getattr(getattr(request.app, "state", None), "redis", None)
        alert_mgr = VoiceAlertManager(redis=redis)
        await alert_mgr.start()
        request.app.state.voice_alert_manager = alert_mgr

    queue = alert_mgr.subscribe(tenant_id)

    async def event_generator():
        try:
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"  # SSE heartbeat
        finally:
            alert_mgr.unsubscribe(tenant_id, queue)

    import asyncio

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
