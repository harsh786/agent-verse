"""Tests for /goals endpoints."""

from __future__ import annotations

import pytest
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.goals import router as goals_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

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


def test_submit_goal_accepts_agent_binding() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = {
        "id": "gid-1",
        "status": "planning",
        "goal": "do it",
        "agent_id": "agent-123",
        "workflow_mode": "multi_agent",
    }
    client = TestClient(_make_app(svc), raise_server_exceptions=False)

    resp = client.post(
        "/goals",
        json={
            "goal": "Investigate failed deployments",
            "agent_id": "agent-123",
            "workflow_mode": "multi_agent",
        },
        headers={"X-API-Key": _VALID_KEY},
    )

    assert resp.status_code == 202
    svc.submit_goal.assert_called_once_with(
        goal="Investigate failed deployments",
        priority="normal",
        dry_run=False,
        tenant_ctx=_CTX,
        agent_id="agent-123",
        workflow_mode="multi_agent",
    )


def test_list_goals_returns_goals_for_tenant() -> None:
    svc = AsyncMock()
    svc.list_goals.return_value = {
        "goals": [
            {
                "id": "gid-1",
                "goal_id": "gid-1",
                "status": "complete",
                "goal": "do it",
                "priority": "normal",
                "dry_run": True,
            }
        ]
    }
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals", headers={"X-API-Key": _VALID_KEY})

    assert resp.status_code == 200
    assert resp.json()["goals"][0]["id"] == "gid-1"
    svc.list_goals.assert_called_once_with(tenant_ctx=_CTX)


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


# ---------------------------------------------------------------------------
# Pause / resume / traces — integration tests using the real application
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_goal_not_running_returns_400(signed_up_client: Any) -> None:
    """Pausing a completed (dry-run) goal returns 400 or 404."""
    import asyncio

    r = await signed_up_client.post("/goals", json={"goal": "dry run", "dry_run": True})
    goal_id = r.json()["goal_id"]
    # Wait for dry run to complete
    await asyncio.sleep(0.1)
    r2 = await signed_up_client.post(f"/goals/{goal_id}/pause")
    assert r2.status_code in (400, 404)  # already complete


@pytest.mark.asyncio
async def test_pause_unknown_goal_returns_404(signed_up_client: Any) -> None:
    r = await signed_up_client.post("/goals/nonexistent-goal/pause")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_resume_unknown_goal_returns_404(signed_up_client: Any) -> None:
    r = await signed_up_client.post("/goals/nonexistent-goal/resume")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_traces_empty_without_db(signed_up_client: Any) -> None:
    r = await signed_up_client.post("/goals", json={"goal": "test", "dry_run": True})
    goal_id = r.json()["goal_id"]
    r2 = await signed_up_client.get(f"/goals/{goal_id}/traces")
    assert r2.status_code == 200
    assert isinstance(r2.json(), list)


@pytest.mark.asyncio
async def test_get_traces_unknown_goal_returns_404(signed_up_client: Any) -> None:
    r = await signed_up_client.get("/goals/nonexistent/traces")
    assert r.status_code == 404
