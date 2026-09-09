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


@router.get("/status", operation_id="voice_status", response_model=VoiceStatusResponse)
async def voice_status(request: Request) -> VoiceStatusResponse:
    _require_tenant(request)
    try:
        from app.voice.providers import get_capabilities

        caps = await get_capabilities()
        return VoiceStatusResponse(
            stt_status=("ready" if caps["stt"]["ready"] else "idle"),
            tts_status=("ready" if caps["tts"]["ready"] else "idle"),
            stt_provider=caps["stt"]["provider"],
            tts_provider=caps["tts"]["provider"],
            stt_model=os.getenv("VOICE_STT_MODEL", "large-v3-turbo"),
            tts_model=os.getenv("VOICE_TTS_MODEL", "k2-fsa/OmniVoice"),
            device=os.getenv("VOICE_DEVICE", "cpu"),
        )
    except Exception:
        from app.voice import stt_engine, tts_engine

        return VoiceStatusResponse(
            stt_status="ready" if stt_engine._model else "idle",
            tts_status="ready" if tts_engine._model else "idle",
            stt_model=os.getenv("VOICE_STT_MODEL", "large-v3-turbo"),
            tts_model=os.getenv("VOICE_TTS_MODEL", "k2-fsa/OmniVoice"),
            device=os.getenv("VOICE_DEVICE", "cpu"),
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
    await _cache_persona(request.app, tenant_id, org_id, content, ref_text, language)
    url = await _store_persona_audio(tenant_id, org_id, content)
    import datetime

    return PersonaResponse(
        org_id=org_id,
        tenant_id=tenant_id,
        ref_audio_url=url,
        ref_text=ref_text,
        language=language,
        created_at=datetime.datetime.utcnow().isoformat(),
    )


@router.delete(
    "/persona/{org_id}", operation_id="voice_persona_delete", status_code=status.HTTP_204_NO_CONTENT
)
async def voice_persona_delete(org_id: str, request: Request) -> None:
    ctx = _require_tenant(request)
    await _delete_persona(request.app, _tenant_id(ctx), org_id)


@router.websocket("/stream/{org_id}")
async def voice_stream(
    ws: WebSocket,
    org_id: str,
    api_key: str = Query(default=""),
    consent: bool = Query(default=False),
) -> None:
    """D-1/D-3/D-4: Real-time voice session — speak goals, approve missions.

    D-24: audio processing requires recorded consent. The client either passes
    ``?consent=true`` at connect time or sends a ``{"type":"consent"}`` control
    message; without it the session fails closed and refuses to transcribe.
    """
    await ws.accept()
    tenant_id = await _ws_auth(ws, api_key)
    if not tenant_id:
        await ws.close(code=4001, reason="Unauthorized")
        return

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
        "overall_health": "healthy",
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


async def _get_persona(app: Any, tid: str, org_id: str) -> tuple[bytes | None, str | None]:
    try:
        redis = getattr(getattr(app, "state", None), "redis", None)
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
    try:
        redis = getattr(getattr(app, "state", None), "redis", None)
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
    except Exception:
        pass


async def _delete_persona(app: Any, tid: str, org_id: str) -> None:
    try:
        redis = getattr(getattr(app, "state", None), "redis", None)
        if redis:
            await redis.delete(f"voice:persona:{tid}:{org_id}")
    except Exception:
        pass


async def _redis_get(app: Any, key: str) -> bytes | None:
    try:
        redis = getattr(getattr(app, "state", None), "redis", None)
        return await redis.get(key) if redis else None
    except Exception:
        return None


async def _redis_set(app: Any, key: str, value: bytes, ttl: int) -> None:
    try:
        redis = getattr(getattr(app, "state", None), "redis", None)
        if redis:
            await redis.setex(key, ttl, value)
    except Exception:
        pass


async def _store_persona_audio(tid: str, org_id: str, audio: bytes) -> str:
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
    except Exception:
        return f"local://{tid}/{org_id}/ref.wav"


async def _ws_auth(ws: WebSocket, api_key: str) -> str | None:
    """Authenticate WebSocket using the same key resolver as TenantMiddleware."""
    if not api_key:
        return None
    try:
        # Use the same _tenant_key_resolver that TenantMiddleware uses
        resolver = getattr(getattr(ws.app, "state", None), "_tenant_key_resolver", None)  # type: ignore[attr-defined]
        if resolver is None:
            # Dev mode fallback — no resolver wired
            return api_key
        tenant_ctx = await resolver(api_key)
        if tenant_ctx is None:
            return None
        return str(getattr(tenant_ctx, "tenant_id", None) or getattr(tenant_ctx, "id", None))
    except Exception:
        return None


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
