"""FastAPI router for Voice — STT transcription and goal refinement.

All endpoints:
  - Require tenant authentication via TenantMiddleware
  - Return RFC 7807 errors on failure
  - Include operation_id for OpenAPI
"""
from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

import httpx
import structlog
from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile, status
from opentelemetry import trace
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

router = APIRouter(prefix="/v1/voice", tags=["voice"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class TranscribeResponse(BaseModel):
    transcript: str
    confidence: float = Field(ge=0.0, le=1.0)
    language: str = "en"


class GoalRefinementRequest(BaseModel):
    transcript: str = Field(min_length=1, max_length=4096, description="Raw spoken text")
    org_id: str | None = Field(default=None, description="Organisation context for goal refinement")


class GoalRefinementResponse(BaseModel):
    goal: str = Field(description="Refined mission goal suitable for submission")
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_priority: str = Field(
        default="medium",
        description="Suggested priority: low | medium | high | critical",
    )
    raw_transcript: str


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _request_id() -> str:
    return str(uuid4())


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/transcribe",
    operation_id="voice_transcribe",
    summary="Convert audio blob to text transcript (STT)",
    response_model=TranscribeResponse,
    status_code=status.HTTP_200_OK,
)
async def voice_transcribe(
    request: Request,
    audio: UploadFile = File(description="Audio file (wav/webm/ogg/mp4, max 10 MB)"),
    x_request_id: str = Header(default_factory=_request_id),
) -> TranscribeResponse:
    """Transcribe an uploaded audio clip using the configured STT backend.

    When no STT backend is configured (``WHISPER_API_KEY`` / ``ASSEMBLY_AI_KEY``
    absent) returns a stub with ``confidence=0`` so the caller knows to fall back
    to the browser's Web Speech API.
    """
    with tracer.start_as_current_span("voice.transcribe") as span:
        ctx = _require_tenant(request)
        tenant_id: str = getattr(ctx, "tenant_id", str(ctx))
        span.set_attribute("tenant_id", tenant_id)
        span.set_attribute("request_id", x_request_id)

        # Size guard (10 MB)
        content = await audio.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={
                    "type": "file-too-large",
                    "title": "Audio too large",
                    "status": 413,
                    "detail": "Audio file must be ≤ 10 MB",
                    "request_id": x_request_id,
                },
            )

        span.set_attribute("audio_bytes", len(content))
        span.set_attribute("audio_content_type", audio.content_type or "unknown")

        try:
            transcript = await _call_stt_backend(content, audio.content_type or "audio/webm")
        except Exception as exc:
            log.error("voice.transcribe.failed", tenant_id=tenant_id, error=str(exc))
            span.record_exception(exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "type": "stt-error",
                    "title": "Transcription failed",
                    "status": 502,
                    "detail": str(exc),
                    "request_id": x_request_id,
                },
            ) from exc

        log.info("voice.transcribe.complete", tenant_id=tenant_id, chars=len(transcript.transcript))
        return transcript


@router.post(
    "/goal",
    operation_id="voice_refine_goal",
    summary="Refine a raw voice transcript into a mission goal",
    response_model=GoalRefinementResponse,
    status_code=status.HTTP_200_OK,
)
async def voice_refine_goal(
    body: GoalRefinementRequest,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
) -> GoalRefinementResponse:
    """Use the LLM provider to turn spoken text into a clean, actionable mission
    goal.  If no LLM provider is available, returns the transcript unchanged.
    """
    with tracer.start_as_current_span("voice.goal_refine") as span:
        ctx = _require_tenant(request)
        tenant_id: str = getattr(ctx, "tenant_id", str(ctx))
        span.set_attribute("tenant_id", tenant_id)
        span.set_attribute("request_id", x_request_id)
        span.set_attribute("transcript_len", len(body.transcript))

        try:
            refined = await _call_goal_refinement(body.transcript, body.org_id, tenant_id)
        except Exception as exc:
            log.warning("voice.goal_refine.fallback", tenant_id=tenant_id, error=str(exc))
            # Graceful degradation — return transcript as-is
            refined = GoalRefinementResponse(
                goal=body.transcript,
                confidence=0.5,
                suggested_priority="medium",
                raw_transcript=body.transcript,
            )

        span.set_attribute("goal_len", len(refined.goal))
        log.info(
            "voice.goal_refine.complete",
            tenant_id=tenant_id,
            priority=refined.suggested_priority,
        )
        return refined


# ── STT / LLM backends (pluggable) ───────────────────────────────────────────

async def _call_stt_backend(audio_bytes: bytes, content_type: str) -> TranscribeResponse:
    """Delegate to a real STT service when configured; otherwise return stub.

    Supported backends (resolved via env vars in priority order):
      1. OpenAI Whisper  (``OPENAI_API_KEY`` present)
      2. AssemblyAI      (``ASSEMBLY_AI_KEY`` present)
      3. Stub            (returns empty transcript, confidence=0)
    """

    if os.getenv("OPENAI_API_KEY"):
        return await _whisper_transcribe(audio_bytes, content_type)

    if os.getenv("ASSEMBLY_AI_KEY"):
        return await _assemblyai_transcribe(audio_bytes)

    # No STT configured — browser handles transcription client-side
    return TranscribeResponse(transcript="", confidence=0.0, language="en")


async def _whisper_transcribe(audio_bytes: bytes, content_type: str) -> TranscribeResponse:
    """Call OpenAI Whisper-1 for transcription."""
    import io

    ext_map = {
        "audio/webm": "webm",
        "audio/wav": "wav",
        "audio/mp4": "mp4",
        "audio/ogg": "ogg",
    }
    ext = ext_map.get(content_type, "webm")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            files={"file": (f"audio.{ext}", io.BytesIO(audio_bytes), content_type)},
            data={"model": "whisper-1"},
        )
        resp.raise_for_status()
        data = resp.json()
        text: str = data.get("text", "")
        return TranscribeResponse(
            transcript=text, confidence=0.95, language=data.get("language", "en")
        )


async def _assemblyai_transcribe(audio_bytes: bytes) -> TranscribeResponse:
    """Upload to AssemblyAI and poll for transcript."""
    import asyncio

    api_key = os.environ["ASSEMBLY_AI_KEY"]
    headers = {"authorization": api_key}
    async with httpx.AsyncClient(timeout=60) as client:
        # 1 — upload
        up = await client.post(
            "https://api.assemblyai.com/v2/upload",
            headers={**headers, "content-type": "application/octet-stream"},
            content=audio_bytes,
        )
        up.raise_for_status()
        upload_url: str = up.json()["upload_url"]

        # 2 — submit transcript job
        sub = await client.post(
            "https://api.assemblyai.com/v2/transcript",
            headers=headers,
            json={"audio_url": upload_url},
        )
        sub.raise_for_status()
        job_id: str = sub.json()["id"]

        # 3 — poll (max 60 s)
        import asyncio

        for _ in range(30):
            await asyncio.sleep(2)
            poll = await client.get(
                f"https://api.assemblyai.com/v2/transcript/{job_id}",
                headers=headers,
            )
            poll.raise_for_status()
            data = poll.json()
            if data["status"] == "completed":
                return TranscribeResponse(
                    transcript=data.get("text", ""), confidence=0.9, language="en"
                )
            if data["status"] == "error":
                raise RuntimeError(data.get("error", "AssemblyAI error"))

    raise TimeoutError("AssemblyAI transcription timed out")


async def _call_goal_refinement(
    transcript: str, org_id: str | None, tenant_id: str
) -> GoalRefinementResponse:
    """Use the LLM provider to refine a transcript into a mission goal."""
    from app.main import app as _app

    provider = getattr(_app.state, "provider", None)
    if provider is None:
        return GoalRefinementResponse(
            goal=transcript.strip(),
            confidence=0.5,
            suggested_priority="medium",
            raw_transcript=transcript,
        )

    from app.providers.base import CompletionRequest, Message

    system = (
        "You are a mission planning assistant. "
        "Convert the following spoken transcript into a concise, actionable mission goal. "
        "Return JSON: {\"goal\": \"...\", \"priority\": \"low|medium|high|critical\"}"
    )
    req = CompletionRequest(
        messages=[
            Message(role="system", content=system),
            Message(role="user", content=transcript),
        ],
        max_tokens=256,
        temperature=0.2,
    )
    result = await provider.complete(req)
    import json as _json

    try:
        parsed = _json.loads(result.content)
        return GoalRefinementResponse(
            goal=parsed.get("goal", transcript.strip()),
            confidence=0.9,
            suggested_priority=parsed.get("priority", "medium"),
            raw_transcript=transcript,
        )
    except Exception:
        return GoalRefinementResponse(
            goal=result.content.strip() or transcript.strip(),
            confidence=0.7,
            suggested_priority="medium",
            raw_transcript=transcript,
        )
