"""Comprehensive tests for /rpa API endpoints — targets 29% → 65%+ coverage."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.rpa import router as rpa_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-rpa", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_rpa_comp"


def _make_app() -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(rpa_router)
    return app


def _make_session() -> MagicMock:
    s = MagicMock()
    s.session_id = "sess-1"
    s.status = "active"
    s.created_at = "2024-01-01T00:00:00Z"
    s.last_used_at = "2024-01-01T01:00:00Z"
    return s


# ---------------------------------------------------------------------------
# list_rpa_tools (auth required via TenantMiddleware)
# ---------------------------------------------------------------------------

def test_list_rpa_tools() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/rpa/tools", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    tools = resp.json()
    assert isinstance(tools, list)
    assert len(tools) > 0
    # Each tool should have a name
    assert all("name" in t for t in tools)


def test_list_rpa_tools_requires_auth() -> None:
    """TenantMiddleware requires auth for all routes."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/rpa/tools")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# execute_rpa_tool
# ---------------------------------------------------------------------------

def test_execute_rpa_tool_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/rpa/execute", json={"tool_name": "browser_navigate", "arguments": {}})
    assert resp.status_code == 401


def test_execute_rpa_tool_unknown_tool() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/rpa/execute",
        json={"tool_name": "nonexistent_tool", "arguments": {}},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 400
    assert "Unknown RPA tool" in resp.json()["detail"]


def test_execute_rpa_tool_success(monkeypatch) -> None:
    result = MagicMock()
    result.success = True
    result.output = "Navigated to https://example.com"
    result.artifact_url = None
    result.artifact_name = None
    result.duration_ms = 250.0
    result.error = None

    class MockExecutor:
        async def execute(self, tool_name, arguments, session_id, tenant_id):
            return result

    monkeypatch.setattr("app.rpa.executor.RPAExecutor", MockExecutor)

    # Get a valid tool name from the catalog
    from app.rpa.tools import RPA_TOOLS
    valid_tool = RPA_TOOLS[0]["name"]

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/rpa/execute",
        json={"tool_name": valid_tool, "arguments": {"url": "https://example.com"}},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["tool_name"] == valid_tool


def test_execute_rpa_tool_with_session_id(monkeypatch) -> None:
    result = MagicMock()
    result.success = True
    result.output = "Done"
    result.artifact_url = None
    result.artifact_name = None
    result.duration_ms = 100.0
    result.error = None

    class MockExecutor:
        async def execute(self, tool_name, arguments, session_id, tenant_id):
            return result

    monkeypatch.setattr("app.rpa.executor.RPAExecutor", MockExecutor)

    from app.rpa.tools import RPA_TOOLS
    valid_tool = RPA_TOOLS[0]["name"]

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/rpa/execute",
        json={"tool_name": valid_tool, "arguments": {}, "session_id": "sess-existing"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "sess-existing"


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def test_list_sessions_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/rpa/sessions")
    assert resp.status_code == 401


def test_list_sessions_empty(monkeypatch) -> None:
    class MockSessionStore:
        def __init__(self):
            pass
        async def list_active(self, tenant_id):
            return []

    monkeypatch.setattr("app.rpa.session.RPASessionStore", MockSessionStore)
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/rpa/sessions", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_session_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/rpa/sessions")
    assert resp.status_code == 401


def test_create_session_success(monkeypatch) -> None:
    session = _make_session()

    class MockSessionStore:
        def __init__(self):
            pass
        async def create(self, tenant_id):
            return session

    monkeypatch.setattr("app.rpa.session.RPASessionStore", MockSessionStore)
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/rpa/sessions", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 201
    body = resp.json()
    assert body["session_id"] == "sess-1"
    assert body["status"] == "active"


def test_close_session_success(monkeypatch) -> None:
    class MockSessionStore:
        def __init__(self):
            pass
        async def close(self, session_id, tenant_id):
            return True

    monkeypatch.setattr("app.rpa.session.RPASessionStore", MockSessionStore)
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete("/rpa/sessions/sess-1", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204


def test_close_session_not_found(monkeypatch) -> None:
    class MockSessionStore:
        def __init__(self):
            pass
        async def close(self, session_id, tenant_id):
            return False

    app = _make_app()
    store = MockSessionStore()
    app.state.rpa_session_store = store
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.delete("/rpa/sessions/nonexistent", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


def test_close_session_no_store() -> None:
    """Closing a session when no store exists returns 204 (graceful no-op)."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete("/rpa/sessions/sess-1", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Screenshot (no session manager)
# ---------------------------------------------------------------------------

def test_get_session_screenshot_no_manager() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/rpa/sessions/sess-1/screenshot", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 503


def test_get_current_view_no_manager() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/rpa/sessions/sess-1/current-view", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Human takeover
# ---------------------------------------------------------------------------

def test_request_takeover_no_store() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/rpa/sessions/sess-1/takeover",
        json={"reason": "Need human help"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 503


def test_request_takeover_session_not_found(monkeypatch) -> None:
    class MockSessionStore:
        def __init__(self):
            pass
        async def get(self, session_id, tenant_id):
            return None

    monkeypatch.setattr("app.rpa.session.RPASessionStore", MockSessionStore)
    app = _make_app()
    store = MockSessionStore()
    app.state.rpa_session_store = store
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/rpa/sessions/nonexistent/takeover",
        json={"reason": "Help"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


def test_request_takeover_success(monkeypatch) -> None:
    session = _make_session()

    class MockSessionStore:
        def __init__(self):
            pass
        async def get(self, session_id, tenant_id):
            return session

    app = _make_app()
    store = MockSessionStore()
    app.state.rpa_session_store = store
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/rpa/sessions/sess-1/takeover",
        json={"reason": "Operator requested assistance"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "sess-1"
    assert body["status"] == "awaiting_human"
