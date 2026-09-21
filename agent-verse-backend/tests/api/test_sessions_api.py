"""Tests for /auth/sessions API endpoints (app/api/sessions.py).

Covers: list active sessions (with/without Redis, decode branches, error
fallback), revoke a single session, and revoke-all-other-sessions — plus the
401 "no tenant" guard on every handler (exercised by calling the handler
directly, since TenantMiddleware itself would 401 before the handler runs).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.sessions import (
    list_active_sessions,
    revoke_all_other_sessions,
    revoke_session,
    router as sessions_router,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-sessions", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_sessionskey"


def _make_app(redis: object | None = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(sessions_router)
    app.state._rate_limiter_redis = redis
    return app


AUTH_HEADERS = {"X-API-Key": _VALID_KEY}


# ---------------------------------------------------------------------------
# GET /auth/sessions
# ---------------------------------------------------------------------------

def test_list_sessions_no_redis_returns_current_only() -> None:
    client = TestClient(_make_app(redis=None), raise_server_exceptions=False)
    resp = client.get("/auth/sessions", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["is_current"] is True
    assert body[0]["session_id"] == "current"


def test_list_sessions_no_api_key_401_via_middleware() -> None:
    client = TestClient(_make_app(redis=None), raise_server_exceptions=False)
    resp = client.get("/auth/sessions")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_sessions_handler_no_tenant_raises_401() -> None:
    req = MagicMock()
    req.state.tenant = None
    with pytest.raises(HTTPException) as exc_info:
        await list_active_sessions(req)
    assert exc_info.value.status_code == 401


def test_list_sessions_with_redis_bytes_data() -> None:
    """Redis returns bytes keys/values throughout — exercises the bytes-decode branch."""
    mock_redis = AsyncMock()
    mock_redis.keys = AsyncMock(
        return_value=[b"session:tid-sessions:s1", b"session:tid-sessions:s2"]
    )
    mock_redis.hgetall = AsyncMock(
        side_effect=[
            {b"created_at": b"2024-01-01T00:00:00", b"ip": b"1.2.3.4", b"ua": b"chrome"},
            {},  # empty hash — should be skipped
        ]
    )
    client = TestClient(_make_app(redis=mock_redis), raise_server_exceptions=False)
    resp = client.get("/auth/sessions", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1  # empty hash entry skipped
    assert body[0]["session_id"] == "s1"
    assert body[0]["created_at"] == "2024-01-01T00:00:00"
    assert body[0]["ip_address"] == "1.2.3.4"
    assert body[0]["user_agent"] == "chrome"
    assert body[0]["is_current"] is False


def test_list_sessions_with_redis_str_data() -> None:
    """Redis returns plain str keys/values (e.g. decode_responses=True clients)."""
    mock_redis = AsyncMock()
    mock_redis.keys = AsyncMock(return_value=["session:tid-sessions:s1"])
    mock_redis.hgetall = AsyncMock(
        return_value={"created_at": "2024-02-02T00:00:00", "ip": "9.9.9.9", "ua": "firefox"}
    )
    client = TestClient(_make_app(redis=mock_redis), raise_server_exceptions=False)
    resp = client.get("/auth/sessions", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["session_id"] == "s1"
    assert body[0]["created_at"] == "2024-02-02T00:00:00"
    assert body[0]["ip_address"] == "9.9.9.9"
    assert body[0]["user_agent"] == "firefox"


def test_list_sessions_redis_error_returns_empty_list() -> None:
    mock_redis = AsyncMock()
    mock_redis.keys = AsyncMock(side_effect=RuntimeError("redis down"))
    client = TestClient(_make_app(redis=mock_redis), raise_server_exceptions=False)
    resp = client.get("/auth/sessions", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_sessions_missing_fields_default_gracefully() -> None:
    """Hash present but missing created_at/ip/ua keys — defaults used, no crash."""
    mock_redis = AsyncMock()
    mock_redis.keys = AsyncMock(return_value=[b"session:tid-sessions:s9"])
    mock_redis.hgetall = AsyncMock(return_value={b"unrelated": b"value"})
    client = TestClient(_make_app(redis=mock_redis), raise_server_exceptions=False)
    resp = client.get("/auth/sessions", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["session_id"] == "s9"
    assert body[0]["created_at"] == ""
    assert body[0]["ip_address"] == "unknown"
    assert body[0]["user_agent"] == ""


# ---------------------------------------------------------------------------
# DELETE /auth/sessions/{session_id}
# ---------------------------------------------------------------------------

def test_revoke_session_no_redis_returns_503() -> None:
    client = TestClient(_make_app(redis=None), raise_server_exceptions=False)
    resp = client.delete("/auth/sessions/s1", headers=AUTH_HEADERS)
    assert resp.status_code == 503


def test_revoke_session_success() -> None:
    mock_redis = AsyncMock()
    mock_redis.delete = AsyncMock(return_value=1)
    client = TestClient(_make_app(redis=mock_redis), raise_server_exceptions=False)
    resp = client.delete("/auth/sessions/s1", headers=AUTH_HEADERS)
    assert resp.status_code == 204
    mock_redis.delete.assert_awaited_once_with("session:tid-sessions:s1")


@pytest.mark.asyncio
async def test_revoke_session_handler_no_tenant_raises_401() -> None:
    req = MagicMock()
    req.state.tenant = None
    with pytest.raises(HTTPException) as exc_info:
        await revoke_session("sid", req)
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /auth/sessions (revoke all others)
# ---------------------------------------------------------------------------

def test_revoke_all_no_redis_returns_503() -> None:
    client = TestClient(_make_app(redis=None), raise_server_exceptions=False)
    resp = client.delete("/auth/sessions", headers=AUTH_HEADERS)
    assert resp.status_code == 503


def test_revoke_all_with_keys_deletes_them() -> None:
    mock_redis = AsyncMock()
    mock_redis.keys = AsyncMock(return_value=[b"session:tid-sessions:s1", b"session:tid-sessions:s2"])
    mock_redis.delete = AsyncMock(return_value=2)
    client = TestClient(_make_app(redis=mock_redis), raise_server_exceptions=False)
    resp = client.delete("/auth/sessions", headers=AUTH_HEADERS)
    assert resp.status_code == 204
    mock_redis.delete.assert_awaited_once_with(
        b"session:tid-sessions:s1", b"session:tid-sessions:s2"
    )


def test_revoke_all_no_keys_skips_delete_call() -> None:
    mock_redis = AsyncMock()
    mock_redis.keys = AsyncMock(return_value=[])
    mock_redis.delete = AsyncMock()
    client = TestClient(_make_app(redis=mock_redis), raise_server_exceptions=False)
    resp = client.delete("/auth/sessions", headers=AUTH_HEADERS)
    assert resp.status_code == 204
    mock_redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoke_all_handler_no_tenant_raises_401() -> None:
    req = MagicMock()
    req.state.tenant = None
    with pytest.raises(HTTPException) as exc_info:
        await revoke_all_other_sessions(req)
    assert exc_info.value.status_code == 401
