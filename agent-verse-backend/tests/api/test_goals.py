"""Tests for /goals endpoints."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware
from app.api.goals import router as goals_router

_CTX = TenantContext(tenant_id="tid-test", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "ak_test_goals123"


def _make_app(fake_service: Any) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(goals_router)
    app.state.goal_service = fake_service
    return app


def test_submit_goal_returns_202_accepted() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = {"id": "gid-1", "status": "planning", "goal": "do it"}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/goals",
        json={"goal": "Fix the memory leak in checkout service"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    assert resp.json()["id"] == "gid-1"


def test_submit_goal_requires_auth() -> None:
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/goals", json={"goal": "do it"})
    assert resp.status_code == 401


def test_submit_goal_validates_empty_goal() -> None:
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/goals", json={"goal": ""}, headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 422


def test_get_goal_returns_status() -> None:
    svc = AsyncMock()
    svc.get_goal.return_value = {
        "id": "gid-1",
        "status": "complete",
        "goal": "do it",
        "steps": [],
    }
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals/gid-1", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["status"] == "complete"


def test_get_nonexistent_goal_returns_404() -> None:
    from app.core.errors import NotFoundError

    svc = AsyncMock()
    svc.get_goal.side_effect = NotFoundError("Goal not found")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals/ghost", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


def test_cancel_goal_returns_200() -> None:
    svc = AsyncMock()
    svc.cancel_goal.return_value = {"id": "gid-1", "status": "cancelled"}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/goals/gid-1/cancel", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
