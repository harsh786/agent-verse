"""Extra coverage for app/voice/router.py beyond test_voice_router.py.

Targets the branches the original suite doesn't reach: the voice_status
exception fallback, transcribe/speak/greeting error paths, persona-fetch
usage in /speak, greeting language auto-detection (success + failure),
greeting cache-hit short-circuit, the /persona/{org_id} size guard, the
proactive-alerts lazy manager creation, the websocket /stream handler, and
every private helper (_fetch_org_health, _fetch_wywa, _get_persona,
_cache_persona, _delete_persona, _redis_get/_redis_set,
_store_persona_audio, _ws_auth).
"""
from __future__ import annotations

import importlib
import io
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.voice.router import router as voice_router

# `app/voice/__init__.py` does `from .router import router`, which overwrites the
# `app.voice.router` *package attribute* with the APIRouter instance — so
# `import app.voice.router as x` would bind `x` to that router, not the module.
# Go through sys.modules (via importlib) to get the actual module object.
voice_router_module = importlib.import_module("app.voice.router")

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


def _make_silent_wav(duration_s: float = 0.1, sr: int = 16_000) -> bytes:
    import numpy as np
    import soundfile as sf

    audio = np.zeros(int(sr * duration_s), dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


@pytest.fixture
async def client() -> AsyncClient:  # type: ignore[misc]
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── GET /v1/voice/status — exception fallback ────────────────────────────────


@pytest.mark.asyncio
async def test_voice_status_exception_falls_back_to_engine_state(client: AsyncClient) -> None:
    from app.voice import stt_engine, tts_engine

    with patch("app.voice.providers.get_capabilities", AsyncMock(side_effect=RuntimeError("boom"))):
        stt_engine._model = None
        tts_engine._model = None
        resp = await client.get("/v1/voice/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["stt_status"] == "idle"
    assert data["tts_status"] == "idle"


# ── POST /v1/voice/transcribe — STT error ────────────────────────────────────


@pytest.mark.asyncio
async def test_transcribe_stt_error_returns_502(client: AsyncClient) -> None:
    with patch("app.voice.router.transcribe", AsyncMock(side_effect=RuntimeError("stt died"))):
        resp = await client.post(
            "/v1/voice/transcribe",
            files={"audio": ("t.wav", _make_silent_wav(), "audio/wav")},
        )
    assert resp.status_code == 502
    assert resp.json()["detail"]["type"] == "stt-error"


# ── POST /v1/voice/speak — persona fetch + TTS error ─────────────────────────


@pytest.mark.asyncio
async def test_speak_with_org_persona_fetches_persona(client: AsyncClient) -> None:
    mock_wav = _make_silent_wav()
    with patch(
        "app.voice.router._get_persona", AsyncMock(return_value=(b"ref-audio", "ref text"))
    ) as mock_get_persona, patch("app.voice.router.synthesize", AsyncMock(return_value=mock_wav)):
        resp = await client.post(
            "/v1/voice/speak",
            json={"text": "hi", "org_id": "org-9", "use_org_persona": True},
        )
    assert resp.status_code == 200
    mock_get_persona.assert_awaited_once()


@pytest.mark.asyncio
async def test_speak_tts_error_returns_502(client: AsyncClient) -> None:
    with patch("app.voice.router.synthesize", AsyncMock(side_effect=RuntimeError("tts died"))):
        resp = await client.post("/v1/voice/speak", json={"text": "hi"})
    assert resp.status_code == 502
    assert resp.json()["detail"]["type"] == "tts-error"


# ── GET /v1/voice/greeting/{org_id} ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_greeting_autodetects_language_from_org() -> None:
    org = MagicMock()
    org.jurisdiction = "IN"

    class _FakeSession:
        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *a: Any) -> bool:
            return False

        def begin(self) -> Any:
            return self

    def _session_factory() -> _FakeSession:
        return _FakeSession()

    mock_wav = _make_silent_wav()
    with patch("app.org.service.OrgService.get_organization", AsyncMock(return_value=org)), \
         patch("app.db.rls.sqlalchemy_rls_context", MagicMock(return_value=_FakeSession())), \
         patch("app.voice.router._fetch_org_health", AsyncMock(return_value={})), \
         patch("app.voice.router._fetch_wywa", AsyncMock(return_value=(0, ""))), \
         patch("app.voice.router._get_persona", AsyncMock(return_value=(None, None))), \
         patch("app.voice.router._redis_get", AsyncMock(return_value=None)), \
         patch("app.voice.router._redis_set", AsyncMock()), \
         patch("app.voice.router.synthesize_greeting", AsyncMock(return_value=mock_wav)) as mock_synth:
        app = _make_app()
        app.state.db_session_factory = _session_factory
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/v1/voice/greeting/org-1")
    assert resp.status_code == 200
    _, kwargs = mock_synth.call_args
    assert kwargs["language"] == "hi"  # jurisdiction_to_language("IN") == "hi"


@pytest.mark.asyncio
async def test_greeting_language_autodetect_failure_falls_back_to_en(client: AsyncClient) -> None:
    def _session_factory() -> Any:
        raise RuntimeError("db unavailable")

    mock_wav = _make_silent_wav()
    with patch("app.voice.router._fetch_org_health", AsyncMock(return_value={})), \
         patch("app.voice.router._fetch_wywa", AsyncMock(return_value=(0, ""))), \
         patch("app.voice.router._get_persona", AsyncMock(return_value=(None, None))), \
         patch("app.voice.router._redis_get", AsyncMock(return_value=None)), \
         patch("app.voice.router._redis_set", AsyncMock()), \
         patch("app.voice.router.synthesize_greeting", AsyncMock(return_value=mock_wav)) as mock_synth:
        app = _make_app()
        app.state.db_session_factory = _session_factory
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/v1/voice/greeting/org-1")
    assert resp.status_code == 200
    _, kwargs = mock_synth.call_args
    assert kwargs["language"] == "en"


@pytest.mark.asyncio
async def test_greeting_returns_cached_wav_without_synthesizing(client: AsyncClient) -> None:
    cached_wav = _make_silent_wav()
    with patch("app.voice.router._redis_get", AsyncMock(return_value=cached_wav)), \
         patch("app.voice.router.synthesize_greeting", AsyncMock()) as mock_synth:
        resp = await client.get("/v1/voice/greeting/org-1")
    assert resp.status_code == 200
    assert resp.content == cached_wav
    mock_synth.assert_not_awaited()


@pytest.mark.asyncio
async def test_greeting_tts_error_returns_502(client: AsyncClient) -> None:
    with patch("app.voice.router._fetch_org_health", AsyncMock(return_value={})), \
         patch("app.voice.router._fetch_wywa", AsyncMock(return_value=(0, ""))), \
         patch("app.voice.router._get_persona", AsyncMock(return_value=(None, None))), \
         patch("app.voice.router._redis_get", AsyncMock(return_value=None)), \
         patch("app.voice.router.synthesize_greeting", AsyncMock(side_effect=RuntimeError("boom"))):
        resp = await client.get("/v1/voice/greeting/org-1")
    assert resp.status_code == 502
    assert resp.json()["detail"]["type"] == "tts-error"


# ── POST /v1/voice/persona/{org_id} — size guard ─────────────────────────────


@pytest.mark.asyncio
async def test_persona_upload_too_large(client: AsyncClient) -> None:
    big = b"x" * (6 * 1024 * 1024)
    resp = await client.post(
        "/v1/voice/persona/org-1?ref_text=hi&language=en",
        files={"audio": ("ref.wav", big, "audio/wav")},
    )
    assert resp.status_code == 413


# ── GET /v1/voice/alerts/stream — lazy manager creation ─────────────────────


@pytest.mark.asyncio
async def test_alerts_stream_generator_yields_queued_event() -> None:
    """Drive the endpoint's inner SSE generator directly (bypassing the ASGI
    transport, which has no clean way to end an infinite stream in-process)
    to exercise both the `data:` yield and the keepalive-timeout yield."""
    import asyncio

    from app.voice.alerts import VoiceAlertManager

    mock_mgr = MagicMock(spec=VoiceAlertManager)
    q: asyncio.Queue = asyncio.Queue()
    await q.put({"event_type": "mission_failed", "text": "oops"})
    mock_mgr.subscribe.return_value = q
    mock_mgr.unsubscribe = MagicMock()

    request = MagicMock()
    request.app.state.voice_alert_manager = mock_mgr
    request.state = MagicMock()
    tenant = MagicMock(tenant_id=TENANT_ID)
    request.state.tenant = tenant
    request.is_disconnected = AsyncMock(side_effect=[False, False, True])

    streaming_response = await voice_router_module.voice_alerts_stream(request)
    gen = streaming_response.body_iterator

    first = await gen.__anext__()
    assert "mission_failed" in first

    # Queue is now empty — the next iteration hits the 15s wait_for and times
    # out almost immediately since queue.get() never resolves; patch the
    # timeout down so the test doesn't actually wait 15 real seconds.
    with patch("asyncio.wait_for", AsyncMock(side_effect=TimeoutError)):
        second = await gen.__anext__()
    assert "keepalive" in second

    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    mock_mgr.unsubscribe.assert_called_once_with(TENANT_ID, q)


@pytest.mark.asyncio
async def test_alerts_stream_lazily_creates_manager_when_absent() -> None:
    import asyncio

    from app.voice.alerts import VoiceAlertManager

    mock_mgr = MagicMock(spec=VoiceAlertManager)
    q: asyncio.Queue = asyncio.Queue()
    mock_mgr.subscribe.return_value = q
    mock_mgr.unsubscribe = MagicMock()
    mock_mgr.start = AsyncMock()

    app = _make_app()
    assert getattr(app.state, "voice_alert_manager", None) is None

    with patch("app.voice.alerts.VoiceAlertManager", return_value=mock_mgr):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            async def _status() -> int:
                async with c.stream("GET", "/v1/voice/alerts/stream") as resp:
                    return resp.status_code

            try:
                status = await asyncio.wait_for(_status(), timeout=2.0)
            except TimeoutError:
                status = 200  # still streaming — the manager was already created

    assert status != 404
    mock_mgr.start.assert_awaited_once()
    assert app.state.voice_alert_manager is mock_mgr


# ── Private helpers ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_org_health_no_db_returns_fallback() -> None:
    app = MagicMock()
    app.state = MagicMock(spec=[])  # no db_session_factory attribute
    health = await voice_router_module._fetch_org_health(app, "org-1", "t1")
    assert health["org_id"] == "org-1"
    assert health["overall_health"] == "healthy"


@pytest.mark.asyncio
async def test_fetch_org_health_success() -> None:
    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *a: Any) -> bool:
            return False

        def begin(self) -> Any:
            return self

    app = MagicMock()
    app.state.db_session_factory = lambda: _Session()
    expected = {"org_id": "org-1", "overall_health": "degraded"}
    with patch("app.db.rls.sqlalchemy_rls_context", MagicMock(return_value=_Session())), \
         patch("app.org.service.OrgService.get_org_health", AsyncMock(return_value=expected)):
        health = await voice_router_module._fetch_org_health(MagicMock(state=app.state), "org-1", "t1")
    assert health == expected


@pytest.mark.asyncio
async def test_fetch_org_health_exception_falls_back() -> None:
    def _boom() -> Any:
        raise RuntimeError("db down")

    app = MagicMock()
    app.state.db_session_factory = _boom
    health = await voice_router_module._fetch_org_health(app, "org-1", "t1")
    assert health["overall_health"] == "healthy"


@pytest.mark.asyncio
async def test_fetch_wywa_no_db_returns_zero() -> None:
    app = MagicMock()
    app.state = MagicMock(spec=[])
    count, summary = await voice_router_module._fetch_wywa(app, "org-1", "t1")
    assert count == 0
    assert summary == ""


@pytest.mark.asyncio
async def test_fetch_wywa_success() -> None:
    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *a: Any) -> bool:
            return False

    app = MagicMock()
    app.state.db_session_factory = lambda: _Session()
    app.state.redis = MagicMock()
    digest = MagicMock(missions_completed=[1, 2], pending_approvals=[3])
    with patch("app.db.rls.sqlalchemy_rls_context", MagicMock(return_value=_Session())), \
         patch("app.org.digest.DigestGenerator.generate", AsyncMock(return_value=digest)):
        count, summary = await voice_router_module._fetch_wywa(app, "org-1", "t1")
    assert count == 3
    assert "3 updates" in summary


@pytest.mark.asyncio
async def test_fetch_wywa_exception_returns_zero() -> None:
    def _boom() -> Any:
        raise RuntimeError("digest failed")

    app = MagicMock()
    app.state.db_session_factory = _boom
    app.state.redis = None
    count, summary = await voice_router_module._fetch_wywa(app, "org-1", "t1")
    assert count == 0
    assert summary == ""


@pytest.mark.asyncio
async def test_get_persona_no_redis_returns_none() -> None:
    app = MagicMock()
    app.state = MagicMock(spec=[])
    assert await voice_router_module._get_persona(app, "t1", "org-1") == (None, None)


@pytest.mark.asyncio
async def test_get_persona_found() -> None:
    import base64

    app = MagicMock()
    redis = MagicMock()
    redis.hgetall = AsyncMock(
        return_value={b"audio": base64.b64encode(b"ref-bytes"), b"text": b"hello"}
    )
    app.state.redis = redis
    audio, text = await voice_router_module._get_persona(app, "t1", "org-1")
    assert audio == b"ref-bytes"
    assert text == "hello"


@pytest.mark.asyncio
async def test_get_persona_not_cached_returns_none() -> None:
    app = MagicMock()
    redis = MagicMock()
    redis.hgetall = AsyncMock(return_value={})
    app.state.redis = redis
    assert await voice_router_module._get_persona(app, "t1", "org-1") == (None, None)


@pytest.mark.asyncio
async def test_get_persona_exception_returns_none() -> None:
    app = MagicMock()
    redis = MagicMock()
    redis.hgetall = AsyncMock(side_effect=RuntimeError("redis down"))
    app.state.redis = redis
    assert await voice_router_module._get_persona(app, "t1", "org-1") == (None, None)


@pytest.mark.asyncio
async def test_cache_persona_no_redis_is_noop() -> None:
    app = MagicMock()
    app.state = MagicMock(spec=[])
    await voice_router_module._cache_persona(app, "t1", "org-1", b"audio", "text", "en")


@pytest.mark.asyncio
async def test_cache_persona_success() -> None:
    app = MagicMock()
    redis = MagicMock()
    redis.hset = AsyncMock()
    app.state.redis = redis
    await voice_router_module._cache_persona(app, "t1", "org-1", b"audio", "text", "en")
    redis.hset.assert_awaited_once()


@pytest.mark.asyncio
async def test_cache_persona_exception_is_swallowed() -> None:
    app = MagicMock()
    redis = MagicMock()
    redis.hset = AsyncMock(side_effect=RuntimeError("boom"))
    app.state.redis = redis
    await voice_router_module._cache_persona(app, "t1", "org-1", b"audio", "text", "en")


@pytest.mark.asyncio
async def test_delete_persona_no_redis_is_noop() -> None:
    app = MagicMock()
    app.state = MagicMock(spec=[])
    await voice_router_module._delete_persona(app, "t1", "org-1")


@pytest.mark.asyncio
async def test_delete_persona_success() -> None:
    app = MagicMock()
    redis = MagicMock()
    redis.delete = AsyncMock()
    app.state.redis = redis
    await voice_router_module._delete_persona(app, "t1", "org-1")
    redis.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_persona_exception_is_swallowed() -> None:
    app = MagicMock()
    redis = MagicMock()
    redis.delete = AsyncMock(side_effect=RuntimeError("boom"))
    app.state.redis = redis
    await voice_router_module._delete_persona(app, "t1", "org-1")


@pytest.mark.asyncio
async def test_redis_get_no_redis_returns_none() -> None:
    app = MagicMock()
    app.state = MagicMock(spec=[])
    assert await voice_router_module._redis_get(app, "k") is None


@pytest.mark.asyncio
async def test_redis_get_found() -> None:
    app = MagicMock()
    redis = MagicMock()
    redis.get = AsyncMock(return_value=b"cached")
    app.state.redis = redis
    assert await voice_router_module._redis_get(app, "k") == b"cached"


@pytest.mark.asyncio
async def test_redis_get_exception_returns_none() -> None:
    app = MagicMock()
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=RuntimeError("boom"))
    app.state.redis = redis
    assert await voice_router_module._redis_get(app, "k") is None


@pytest.mark.asyncio
async def test_redis_set_no_redis_is_noop() -> None:
    app = MagicMock()
    app.state = MagicMock(spec=[])
    await voice_router_module._redis_set(app, "k", b"v", 10)


@pytest.mark.asyncio
async def test_redis_set_success() -> None:
    app = MagicMock()
    redis = MagicMock()
    redis.setex = AsyncMock()
    app.state.redis = redis
    await voice_router_module._redis_set(app, "k", b"v", 10)
    redis.setex.assert_awaited_once_with("k", 10, b"v")


@pytest.mark.asyncio
async def test_redis_set_exception_is_swallowed() -> None:
    app = MagicMock()
    redis = MagicMock()
    redis.setex = AsyncMock(side_effect=RuntimeError("boom"))
    app.state.redis = redis
    await voice_router_module._redis_set(app, "k", b"v", 10)


@pytest.mark.asyncio
async def test_store_persona_audio_success_via_s3() -> None:
    mock_boto3 = MagicMock()
    mock_s3 = MagicMock()
    mock_boto3.client.return_value = mock_s3
    with patch.dict("sys.modules", {"boto3": mock_boto3}):
        url = await voice_router_module._store_persona_audio("t1", "org-1", b"audio")
    assert url.startswith("s3://")
    mock_s3.put_object.assert_called_once()


@pytest.mark.asyncio
async def test_store_persona_audio_falls_back_to_local_on_failure() -> None:
    mock_boto3 = MagicMock()
    mock_boto3.client.side_effect = RuntimeError("no creds")
    with patch.dict("sys.modules", {"boto3": mock_boto3}):
        url = await voice_router_module._store_persona_audio("t1", "org-1", b"audio")
    assert url == "local://t1/org-1/ref.wav"


@pytest.mark.asyncio
async def test_ws_auth_no_api_key_returns_none() -> None:
    ws = MagicMock()
    assert await voice_router_module._ws_auth(ws, "") is None


@pytest.mark.asyncio
async def test_ws_auth_no_resolver_dev_fallback() -> None:
    ws = MagicMock()
    ws.app.state = MagicMock(spec=[])
    assert await voice_router_module._ws_auth(ws, "some-key") == "some-key"


@pytest.mark.asyncio
async def test_ws_auth_with_resolver_success() -> None:
    ws = MagicMock()
    tenant_ctx = MagicMock(tenant_id="resolved-tenant")
    ws.app.state._tenant_key_resolver = AsyncMock(return_value=tenant_ctx)
    result = await voice_router_module._ws_auth(ws, "some-key")
    assert result == "resolved-tenant"


@pytest.mark.asyncio
async def test_ws_auth_with_resolver_rejects() -> None:
    ws = MagicMock()
    ws.app.state._tenant_key_resolver = AsyncMock(return_value=None)
    assert await voice_router_module._ws_auth(ws, "some-key") is None


@pytest.mark.asyncio
async def test_ws_auth_resolver_exception_returns_none() -> None:
    ws = MagicMock()
    ws.app.state._tenant_key_resolver = AsyncMock(side_effect=RuntimeError("boom"))
    assert await voice_router_module._ws_auth(ws, "some-key") is None


# ── WebSocket /v1/voice/stream/{org_id} ───────────────────────────────────────


def test_voice_stream_unauthorized_closes_connection() -> None:
    from starlette.websockets import WebSocketDisconnect

    app = FastAPI()
    app.include_router(voice_router)
    client = TestClient(app)
    with patch("app.voice.router._ws_auth", AsyncMock(return_value=None)):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/v1/voice/stream/org-1?api_key=bad") as ws:
                ws.receive_text()
    assert exc_info.value.code == 4001


def test_voice_stream_authorized_autodetects_language_from_org() -> None:
    """Exercises the websocket handler's own DB-backed jurisdiction lookup
    (lines distinct from the /greeting endpoint's — same idea, different code path)."""
    app = FastAPI()
    app.include_router(voice_router)
    client = TestClient(app)

    class _Session:
        def __enter__(self) -> _Session:
            return self

        def __exit__(self, *a: Any) -> bool:
            return False

        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *a: Any) -> bool:
            return False

        def begin(self) -> Any:
            return self

    org = MagicMock()
    org.jurisdiction = "FR"
    mock_session = MagicMock()
    mock_session.run = AsyncMock()

    with patch("app.voice.router._ws_auth", AsyncMock(return_value="t1")), \
         patch("app.voice.router._get_persona", AsyncMock(return_value=(None, None))), \
         patch("app.org.service.OrgService.get_organization", AsyncMock(return_value=org)), \
         patch("app.db.rls.sqlalchemy_rls_context", MagicMock(return_value=_Session())), \
         patch("app.voice.router.VoiceStreamingSession", return_value=mock_session) as mock_cls:
        app.state.db_session_factory = lambda: _Session()
        with client.websocket_connect("/v1/voice/stream/org-1?api_key=good"):
            pass

    _, kwargs = mock_cls.call_args
    assert kwargs["language"] == "fr"  # jurisdiction_to_language("FR") == "fr"


def test_voice_stream_authorized_language_detection_failure_falls_back() -> None:
    app = FastAPI()
    app.include_router(voice_router)
    client = TestClient(app)
    mock_session = MagicMock()
    mock_session.run = AsyncMock()

    def _boom() -> Any:
        raise RuntimeError("db unavailable")

    with patch("app.voice.router._ws_auth", AsyncMock(return_value="t1")), \
         patch("app.voice.router._get_persona", AsyncMock(return_value=(None, None))), \
         patch("app.voice.router.VoiceStreamingSession", return_value=mock_session) as mock_cls:
        app.state.db_session_factory = _boom
        with client.websocket_connect("/v1/voice/stream/org-1?api_key=good"):
            pass

    _, kwargs = mock_cls.call_args
    assert kwargs["language"] == "en"


def test_voice_stream_authorized_runs_session() -> None:
    app = FastAPI()
    app.include_router(voice_router)
    client = TestClient(app)

    mock_session = MagicMock()
    mock_session.run = AsyncMock()

    with patch("app.voice.router._ws_auth", AsyncMock(return_value="t1")), \
         patch("app.voice.router._get_persona", AsyncMock(return_value=(None, None))), \
         patch("app.voice.router.VoiceStreamingSession", return_value=mock_session) as mock_cls:
        with client.websocket_connect("/v1/voice/stream/org-1?api_key=good&consent=true"):
            pass

    mock_cls.assert_called_once()
    mock_session.run.assert_awaited_once()
    _, kwargs = mock_cls.call_args
    assert kwargs["tenant_id"] == "t1"
    assert kwargs["org_id"] == "org-1"
    assert kwargs["consent_granted"] is True
