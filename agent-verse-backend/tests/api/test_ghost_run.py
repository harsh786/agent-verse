"""Tests for POST /goals/ghost-run endpoint.

Covers:
1. All strategies submitted and goal_ids returned
2. Auth required
3. One strategy failing does not block others
4. Default strategies have required fields
5. All goal_ids are unique
6. agent_id is passed through to submit_goal
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.goals import router as goals_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

# ── Fixtures ──────────────────────────────────────────────────────────────────

_CTX = TenantContext(tenant_id="tid-ghost", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "ak_ghost_test123"
_HEADERS = {"X-API-Key": _VALID_KEY}


def _make_app(fake_service: Any) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(goals_router)
    app.state.goal_service = fake_service
    return app


def _counter_svc(base: str = "goal") -> AsyncMock:
    """Service that returns unique goal_ids on each call."""
    svc = AsyncMock()
    seq = [0]

    async def _submit(**kwargs: Any) -> dict[str, Any]:
        seq[0] += 1
        gid = f"{base}-{seq[0]}"
        return {"id": gid, "goal_id": gid, "status": "planning"}

    svc.submit_goal.side_effect = _submit
    return svc


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_ghost_run_submits_all_strategies() -> None:
    """N strategies in request → N goal_ids returned in response."""
    svc = _counter_svc()
    client = TestClient(_make_app(svc))
    resp = client.post(
        "/goals/ghost-run",
        json={
            "goal": "Find the memory leak in checkout service",
            "strategies": [
                {"name": "A", "workflow_mode": "single_agent", "priority": "normal"},
                {"name": "B", "workflow_mode": "single_agent", "priority": "high"},
            ],
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "ghost_run_id" in data
    assert len(data["goal_ids"]) == 2
    assert len(data["strategies"]) == 2
    assert all(s["status"] == "queued" for s in data["strategies"])


def test_ghost_run_requires_auth() -> None:
    """Missing auth header → 401."""
    svc = _counter_svc()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/goals/ghost-run",
        json={"goal": "test"},
        # No X-API-Key header
    )
    assert resp.status_code == 401


def test_ghost_run_handles_failed_strategy() -> None:
    """One failing strategy does not prevent others from succeeding.

    Expected: response still 202, failed strategy has status='failed' with error message,
    successful strategy has status='queued'.
    """
    svc = AsyncMock()
    seq = [0]

    async def _submit(**kwargs: Any) -> dict[str, Any]:
        seq[0] += 1
        if kwargs.get("priority") == "high":
            raise RuntimeError("Simulated high-priority failure")
        gid = f"goal-{seq[0]}"
        return {"id": gid, "goal_id": gid, "status": "planning"}

    svc.submit_goal.side_effect = _submit

    client = TestClient(_make_app(svc))
    resp = client.post(
        "/goals/ghost-run",
        json={
            "goal": "Test partial failure scenario",
            "strategies": [
                {"name": "Good", "workflow_mode": "single_agent", "priority": "normal"},
                {"name": "Bad", "workflow_mode": "single_agent", "priority": "high"},
            ],
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "ghost_run_id" in data

    failed = [s for s in data["strategies"] if s["status"] == "failed"]
    succeeded = [s for s in data["strategies"] if s["status"] == "queued"]
    assert len(failed) == 1
    assert len(succeeded) == 1
    assert failed[0]["name"] == "Bad"
    assert failed[0]["error"] is not None
    assert succeeded[0]["name"] == "Good"
    assert succeeded[0]["goal_id"] is not None


def test_ghost_run_default_strategies_valid() -> None:
    """When no strategies are provided, defaults are used and have required fields."""
    svc = _counter_svc()
    client = TestClient(_make_app(svc))
    resp = client.post(
        "/goals/ghost-run",
        json={"goal": "Use default strategies"},
        headers=_HEADERS,
    )
    assert resp.status_code == 202
    data = resp.json()

    # At least 1 default strategy is returned
    assert len(data["strategies"]) >= 1
    for strategy in data["strategies"]:
        assert "name" in strategy
        assert "status" in strategy
        assert strategy["name"]  # non-empty name


def test_ghost_run_goal_ids_all_different() -> None:
    """Each strategy gets a unique goal_id — no duplicates."""
    svc = _counter_svc("unique")
    client = TestClient(_make_app(svc))
    resp = client.post(
        "/goals/ghost-run",
        json={
            "goal": "Unique ID verification test",
            "strategies": [
                {"name": "S1", "workflow_mode": "single_agent", "priority": "normal"},
                {"name": "S2", "workflow_mode": "single_agent", "priority": "high"},
                {"name": "S3", "workflow_mode": "multi_agent", "priority": "normal"},
            ],
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 202
    data = resp.json()

    goal_id_values = list(data["goal_ids"].values())
    assert len(goal_id_values) == len(set(goal_id_values)), (
        f"Duplicate goal IDs found: {goal_id_values}"
    )
    # Strategy names map to different ids
    assert len(data["goal_ids"]) == 3


def test_ghost_run_respects_agent_id() -> None:
    """agent_id from the strategy request is forwarded to submit_goal."""
    svc = _counter_svc()
    client = TestClient(_make_app(svc))
    resp = client.post(
        "/goals/ghost-run",
        json={
            "goal": "Route to specific agent",
            "strategies": [
                {
                    "name": "AgentBound",
                    "workflow_mode": "single_agent",
                    "priority": "normal",
                    "agent_id": "agent-xyz-789",
                },
            ],
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 202
    # Inspect the call made to submit_goal
    assert svc.submit_goal.call_count == 1
    call_kwargs = svc.submit_goal.call_args_list[0].kwargs
    assert call_kwargs.get("agent_id") == "agent-xyz-789"
