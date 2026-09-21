"""Tests for the per-tenant sandbox API (app/api/sandbox.py)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.sandbox import (
    get_sandbox_config,
    router as sandbox_router,
    submit_sandbox_goal,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-sandbox", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_sandboxkey"
AUTH_HEADERS = {"X-API-Key": _VALID_KEY}


def _make_app(goal_service: object | None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(sandbox_router)
    app.state.goal_service = goal_service
    return app


# ---------------------------------------------------------------------------
# POST /sandbox/goals
# ---------------------------------------------------------------------------

def test_submit_sandbox_goal_no_api_key_401() -> None:
    client = TestClient(_make_app(goal_service=None), raise_server_exceptions=False)
    resp = client.post("/sandbox/goals", json={"goal": "test"})
    assert resp.status_code == 401


def test_submit_sandbox_goal_no_service_503() -> None:
    client = TestClient(_make_app(goal_service=None), raise_server_exceptions=False)
    resp = client.post("/sandbox/goals", json={"goal": "test"}, headers=AUTH_HEADERS)
    assert resp.status_code == 503


def test_submit_sandbox_goal_success() -> None:
    svc = MagicMock()
    svc.submit_goal = AsyncMock(return_value={"goal_id": "g-1", "status": "queued"})
    client = TestClient(_make_app(goal_service=svc), raise_server_exceptions=False)
    resp = client.post(
        "/sandbox/goals",
        json={"goal": "deploy the app", "agent_id": "agent-42"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["goal_id"] == "g-1"
    assert body["sandbox"] is True
    assert "no real tools were called" in body["message"]

    svc.submit_goal.assert_awaited_once()
    _, kwargs = svc.submit_goal.call_args
    assert kwargs["goal"] == "deploy the app"
    assert kwargs["priority"] == "low"
    assert kwargs["dry_run"] is True
    assert kwargs["tenant_ctx"] == _CTX
    assert kwargs["execution_context"]["sandbox_mode"] is True
    assert kwargs["execution_context"]["mock_tools"] is True
    assert kwargs["execution_context"]["agent_id"] == "agent-42"


def test_submit_sandbox_goal_defaults_empty_goal_when_missing() -> None:
    svc = MagicMock()
    svc.submit_goal = AsyncMock(return_value={"goal_id": "g-2"})
    client = TestClient(_make_app(goal_service=svc), raise_server_exceptions=False)
    resp = client.post("/sandbox/goals", json={}, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    _, kwargs = svc.submit_goal.call_args
    assert kwargs["goal"] == ""
    assert kwargs["execution_context"]["agent_id"] is None


@pytest.mark.asyncio
async def test_submit_sandbox_goal_handler_no_tenant_raises_401() -> None:
    req = MagicMock()
    req.state.tenant = None
    with pytest.raises(HTTPException) as exc_info:
        await submit_sandbox_goal(req, {"goal": "x"})
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# GET /sandbox/config
# ---------------------------------------------------------------------------

def test_get_sandbox_config_no_api_key_401() -> None:
    client = TestClient(_make_app(goal_service=None), raise_server_exceptions=False)
    resp = client.get("/sandbox/config")
    assert resp.status_code == 401


def test_get_sandbox_config_success() -> None:
    client = TestClient(_make_app(goal_service=None), raise_server_exceptions=False)
    resp = client.get("/sandbox/config", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["sandbox_enabled"] is True
    assert body["mock_tools"] is True
    assert "limitations" in body and len(body["limitations"]) == 3


@pytest.mark.asyncio
async def test_get_sandbox_config_handler_no_tenant_raises_401() -> None:
    req = MagicMock()
    req.state.tenant = None
    with pytest.raises(HTTPException) as exc_info:
        await get_sandbox_config(req)
    assert exc_info.value.status_code == 401
