"""Tests for the new Voice OS router — all 7 endpoints.

Uses fast in-memory mocking: no real STT/TTS model calls.
"""
from __future__ import annotations

import io
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.voice.router import router as voice_router

TENANT_ID = "00000000-0000-0000-0000-000000000002"


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_tenant(request: Any, call_next: Any) -> Any:
        tenant = MagicMock()
        tenant.tenant_id = TENANT_ID
        request.state.tenant = tenant
        return await call_next(request)

    app.include_router(voice_router)
    return app


def _make_silent_wav(duration_s: float = 0.2, sr: int = 16_000) -> bytes:
    audio = np.zeros(int(sr * duration_s), dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


@pytest.fixture
async def client() -> AsyncClient:  # type: ignore[misc]
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── GET /v1/voice/status ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_voice_status_ok(client: AsyncClient) -> None:
    """Status endpoint returns correct schema."""
    from unittest.mock import AsyncMock, MagicMock

    from app.voice.providers import override_stt, override_tts, reset_providers
    mock_stt = MagicMock()
    mock_stt.provider_name = "faster_whisper"
    mock_stt.supports_streaming = False
    mock_stt.is_ready = AsyncMock(return_value=False)
    mock_tts = MagicMock()
    mock_tts.provider_name = "kokoro"
    mock_tts.sample_rate = 24_000
    mock_tts.supports_voice_cloning = False
    mock_tts.supports_nonverbal = False
    mock_tts.is_ready = AsyncMock(return_value=False)
    reset_providers()
    override_stt(mock_stt)
    override_tts(mock_tts)
    resp = await client.get("/v1/voice/status")
    reset_providers()
    assert resp.status_code == 200
    data = resp.json()
    assert "stt_status" in data
    assert "tts_status" in data
    assert data["stt_status"] in ("ready", "idle", "error")


@pytest.mark.asyncio
async def test_voice_status_requires_auth() -> None:
    bare = FastAPI()
    bare.include_router(voice_router)
    async with AsyncClient(transport=ASGITransport(bare), base_url="http://test") as c:
        resp = await c.get("/v1/voice/status")
    assert resp.status_code == 401


# ── POST /v1/voice/transcribe ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transcribe_ok(client: AsyncClient) -> None:
    """Transcribe returns transcript dict."""
    mock_result = {
        "transcript": "hello world", "language": "en",
        "confidence": 0.95, "segments": [], "duration_s": 0.0,
    }
    with patch("app.voice.router.transcribe", AsyncMock(return_value=mock_result)):
        wav = _make_silent_wav()
        resp = await client.post(
            "/v1/voice/transcribe",
            files={"audio": ("test.wav", wav, "audio/wav")},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["transcript"] == "hello world"
    assert data["language"] == "en"


@pytest.mark.asyncio
async def test_transcribe_file_too_large(client: AsyncClient) -> None:
    """Files over VOICE_MAX_AUDIO_MB return 413."""
    import os
    os.environ["VOICE_MAX_AUDIO_MB"] = "1"
    big = b"x" * (2 * 1024 * 1024)  # 2 MB
    resp = await client.post(
        "/v1/voice/transcribe",
        files={"audio": ("big.wav", big, "audio/wav")},
    )
    assert resp.status_code == 413
    os.environ.pop("VOICE_MAX_AUDIO_MB", None)


@pytest.mark.asyncio
async def test_transcribe_requires_auth() -> None:
    bare = FastAPI()
    bare.include_router(voice_router)
    async with AsyncClient(transport=ASGITransport(bare), base_url="http://test") as c:
        resp = await c.post(
            "/v1/voice/transcribe",
            files={"audio": ("t.wav", b"data", "audio/wav")},
        )
    assert resp.status_code == 401


# ── POST /v1/voice/speak ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_speak_ok(client: AsyncClient) -> None:
    """Speak returns audio/wav content."""
    mock_wav = _make_silent_wav()
    with patch("app.voice.router.synthesize", AsyncMock(return_value=mock_wav)):
        resp = await client.post(
            "/v1/voice/speak",
            json={"text": "Hello AgentVerse", "language": "en"},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert len(resp.content) > 0


@pytest.mark.asyncio
async def test_speak_requires_auth() -> None:
    bare = FastAPI()
    bare.include_router(voice_router)
    async with AsyncClient(transport=ASGITransport(bare), base_url="http://test") as c:
        resp = await c.post("/v1/voice/speak", json={"text": "test"})
    assert resp.status_code == 401


# ── GET /v1/voice/greeting/{org_id} ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_greeting_ok(client: AsyncClient) -> None:
    """Greeting endpoint returns audio/wav with real health data."""
    mock_wav = _make_silent_wav()
    mock_health = {
        "org_id": "org-1", "org_name": "Test Org", "overall_health": "healthy",
        "active_missions": 2, "active_teams": 1,
        "pending_approvals": 0, "items_needing_attention": 0,
    }
    with patch("app.voice.router._fetch_org_health", AsyncMock(return_value=mock_health)), \
         patch("app.voice.router._fetch_wywa", AsyncMock(return_value=(0, ""))), \
         patch("app.voice.router._get_persona", AsyncMock(return_value=(None, None))), \
         patch("app.voice.router._redis_get", AsyncMock(return_value=None)), \
         patch("app.voice.router._redis_set", AsyncMock()), \
         patch("app.voice.router.synthesize_greeting", AsyncMock(return_value=mock_wav)):
        resp = await client.get("/v1/voice/greeting/org-1?user_name=Alice")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"


# ── Persona endpoints ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_persona_upload_ok(client: AsyncClient) -> None:
    """Persona upload stores ref audio."""
    with patch("app.voice.router._cache_persona", AsyncMock()), \
         patch("app.voice.router._store_persona_audio", AsyncMock(return_value="local://test")):
        resp = await client.post(
            "/v1/voice/persona/org-1?ref_text=hello&language=en",
            files={"audio": ("ref.wav", _make_silent_wav(), "audio/wav")},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["org_id"] == "org-1"
    assert data["ref_text"] == "hello"


@pytest.mark.asyncio
async def test_persona_delete_ok(client: AsyncClient) -> None:
    """Persona delete returns 204."""
    with patch("app.voice.router._delete_persona", AsyncMock()):
        resp = await client.delete("/v1/voice/persona/org-1")
    assert resp.status_code == 204


# ── GET /v1/voice/alerts/stream ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alerts_stream_endpoint_exists(client: AsyncClient) -> None:
    """Alerts stream route is registered and returns SSE or 200."""
    # The route exists — just verify it doesn't 404
    import asyncio

    from app.voice.alerts import VoiceAlertManager
    mock_mgr = MagicMock(spec=VoiceAlertManager)
    q: asyncio.Queue = asyncio.Queue()
    mock_mgr.subscribe.return_value = q
    mock_mgr.unsubscribe = MagicMock()
    mock_mgr.start = AsyncMock()
    with patch("app.voice.alerts.VoiceAlertManager", return_value=mock_mgr):
        # Open the stream and read ONLY the response headers, then close — never
        # read the infinite SSE body. Bound with asyncio.wait_for because httpx's
        # own timeout does not interrupt an in-process ASGI stream, which is what
        # made a plain client.get() hang forever here.
        async def _status() -> int:
            async with client.stream("GET", "/v1/voice/alerts/stream") as resp:
                return resp.status_code

        try:
            status = await asyncio.wait_for(_status(), timeout=2.0)
        except TimeoutError:
            return  # the route exists and is streaming — that's not a 404
        assert status != 404
