"""Tests for Voice router — app/voice/router.py.

Uses fast in-memory mocking (no real STT API calls).
"""
from __future__ import annotations

import io
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.voice.router import router as voice_router

# ── Helpers ───────────────────────────────────────────────────────────────────

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


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
async def client() -> AsyncClient:  # type: ignore[misc]
    async with AsyncClient(
        transport=ASGITransport(app=_make_app()),
        base_url="http://test",
    ) as c:
        yield c


# ── Transcribe ────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_transcribe_stub_no_api_key(client: AsyncClient) -> None:
    """Without an API key the stub is returned (transcript='', confidence=0)."""
    audio_bytes = b"RIFF\x00\x00\x00\x00WAVEfmt "
    resp = await client.post(
        "/v1/voice/transcribe",
        files={"audio": ("test.wav", io.BytesIO(audio_bytes), "audio/wav")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["transcript"] == ""
    assert data["confidence"] == 0.0
    assert data["language"] == "en"


@pytest.mark.anyio
async def test_transcribe_file_too_large(client: AsyncClient) -> None:
    """Files > 10 MB must be rejected with 413."""
    big = b"x" * (10 * 1024 * 1024 + 1)
    resp = await client.post(
        "/v1/voice/transcribe",
        files={"audio": ("big.wav", io.BytesIO(big), "audio/wav")},
    )
    assert resp.status_code == 413
    assert resp.json()["detail"]["type"] == "file-too-large"


@pytest.mark.anyio
async def test_transcribe_requires_auth() -> None:
    """Without tenant middleware the endpoint returns 401."""
    bare_app = FastAPI()
    bare_app.include_router(voice_router)
    async with AsyncClient(
        transport=ASGITransport(app=bare_app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/v1/voice/transcribe",
            files={"audio": ("test.wav", io.BytesIO(b"data"), "audio/wav")},
        )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_transcribe_openai_whisper(client: AsyncClient) -> None:
    """When OPENAI_API_KEY is set, Whisper is called and transcript returned."""
    whisper_resp = MagicMock()
    whisper_resp.json.return_value = {"text": "hello world", "language": "en"}
    whisper_resp.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(return_value=whisper_resp)

    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}),
        patch("app.voice.router.httpx.AsyncClient", return_value=mock_http),
    ):
        resp = await client.post(
            "/v1/voice/transcribe",
            files={"audio": ("t.wav", io.BytesIO(b"wav"), "audio/wav")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["transcript"] == "hello world"
    assert data["confidence"] == 0.95


# ── Goal Refinement ───────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_goal_refine_no_provider(client: AsyncClient) -> None:
    """Without an LLM provider the transcript is returned as-is."""
    with patch("app.voice.router._app", create=True) as mock_app:
        mock_app.state.provider = None
        resp = await client.post(
            "/v1/voice/goal",
            json={"transcript": "Improve customer onboarding by 20%"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "onboarding" in data["goal"]
    assert data["raw_transcript"] == "Improve customer onboarding by 20%"


@pytest.mark.anyio
async def test_goal_refine_with_provider(client: AsyncClient) -> None:
    """With a provider the refined goal is returned."""
    from app.voice.router import GoalRefinementResponse

    refined = GoalRefinementResponse(
        goal="Improve onboarding NPS by 20%",
        confidence=0.9,
        suggested_priority="high",
        raw_transcript="Improve customer onboarding",
    )
    with patch("app.voice.router._call_goal_refinement", AsyncMock(return_value=refined)):
        resp = await client.post(
            "/v1/voice/goal",
            json={"transcript": "Improve customer onboarding"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["goal"] == "Improve onboarding NPS by 20%"
    assert data["suggested_priority"] == "high"
    assert data["confidence"] == 0.9


@pytest.mark.anyio
async def test_goal_refine_requires_auth() -> None:
    """Without tenant middleware the endpoint returns 401."""
    bare_app = FastAPI()
    bare_app.include_router(voice_router)
    async with AsyncClient(
        transport=ASGITransport(app=bare_app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/v1/voice/goal",
            json={"transcript": "test"},
        )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_goal_refine_empty_transcript(client: AsyncClient) -> None:
    """Empty transcript must be rejected by Pydantic validation (422)."""
    resp = await client.post(
        "/v1/voice/goal",
        json={"transcript": ""},
    )
    assert resp.status_code == 422
