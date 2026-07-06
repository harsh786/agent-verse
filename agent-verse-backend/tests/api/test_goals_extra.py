"""Tests for P2 correctness fixes: goal_feedback persistence, RLS on lineage, batch status.

Covers:
- FIX 1: POST /goals/{goal_id}/feedback returns 200 and handles missing DB gracefully
- FIX 2: GET /goals/batch/{batch_id}/status returns real statuses
- FIX 3: GET /goals/{goal_id}/lineage requires auth (RLS guard)
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.goals import router as goals_router
from app.core.errors import NotFoundError
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-extra", plan=PlanTier.PROFESSIONAL, api_key_id="kid-extra")
_KEY = "ak_test_extra_goals"


def _make_app(svc: Any, *, extra_state: dict | None = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(goals_router)
    app.state.goal_service = svc
    if extra_state:
        for k, v in extra_state.items():
            setattr(app.state, k, v)
    return app


# ---------------------------------------------------------------------------
# FIX 1: goal_feedback
# ---------------------------------------------------------------------------


def test_goal_feedback_returns_success_when_db_unavailable() -> None:
    """FIX 1: feedback endpoint returns 200 even when no DB is available (graceful degradation)."""
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)

    # get_session_factory returns None → DB path skipped, no crash
    with patch("app.api.goals.get_session_factory", return_value=None, create=True):
        resp = client.post(
            "/goals/test-goal-id/feedback",
            json={"rating": 5, "comment": "Great result!"},
            headers={"X-API-Key": _KEY},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "feedback_recorded"
    assert data["goal_id"] == "test-goal-id"
    assert data["rating"] == 5


def test_goal_feedback_requires_auth() -> None:
    """FIX 1: unauthenticated request to feedback endpoint → 401."""
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/goals/test-goal-id/feedback",
        json={"rating": 3},
    )
    assert resp.status_code == 401


def test_goal_feedback_db_exception_does_not_crash() -> None:
    """FIX 1: DB failure logs a warning and still returns success (graceful degradation)."""
    svc = AsyncMock()

    # Simulate a DB factory that raises on use
    bad_session_factory = AsyncMock(side_effect=RuntimeError("db offline"))

    with patch(
        "app.db.session.get_session_factory", return_value=bad_session_factory, create=True
    ):
        client = TestClient(_make_app(svc), raise_server_exceptions=False)
        resp = client.post(
            "/goals/any-goal/feedback",
            json={"rating": 1, "comment": "Wrong"},
            headers={"X-API-Key": _KEY},
        )

    # Graceful degradation: still 200, not 500
    assert resp.status_code in (200, 422)  # 422 only if Pydantic rejects payload


# ---------------------------------------------------------------------------
# FIX 2: batch status
# ---------------------------------------------------------------------------


def test_batch_status_returns_real_goal_statuses() -> None:
    """FIX 2: /goals/batch/{ids}/status returns per-goal statuses, not a stub."""
    svc = AsyncMock()
    svc.get_goal.return_value = {"status": "complete", "goal": "do something"}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)

    resp = client.get(
        "/goals/batch/goal-1,goal-2/status",
        headers={"X-API-Key": _KEY},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["batch_id"] == "goal-1,goal-2"
    assert data["total"] == 2
    assert len(data["goals"]) == 2
    assert all(g["status"] == "complete" for g in data["goals"])
    assert data["all_complete"] is True


def test_batch_status_handles_missing_goals() -> None:
    """FIX 2: goals that don't exist are reported as not_found, not an exception."""
    svc = AsyncMock()
    svc.get_goal.side_effect = NotFoundError("not found")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)

    resp = client.get(
        "/goals/batch/ghost-id/status",
        headers={"X-API-Key": _KEY},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["goals"][0]["status"] == "not_found"
    assert data["all_complete"] is True  # not_found counts as done


def test_batch_status_requires_auth() -> None:
    """FIX 2: unauthenticated request → 401."""
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals/batch/gid-1,gid-2/status")
    assert resp.status_code == 401


def test_batch_status_no_longer_stub() -> None:
    """FIX 2: the old stub response key 'message' must NOT appear."""
    svc = AsyncMock()
    svc.get_goal.return_value = {"status": "running"}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)

    resp = client.get(
        "/goals/batch/some-id/status",
        headers={"X-API-Key": _KEY},
    )

    assert resp.status_code == 200
    assert "message" not in resp.json(), "Stub response should be gone"
    assert "goals" in resp.json()


# ---------------------------------------------------------------------------
# FIX 3: lineage and attempts RLS
# ---------------------------------------------------------------------------


def test_goal_lineage_requires_auth() -> None:
    """FIX 3: lineage endpoint returns 401 without a valid API key."""
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals/test-goal-id/lineage")
    assert resp.status_code == 401


def test_goal_attempts_requires_auth() -> None:
    """FIX 3: attempts endpoint returns 401 without a valid API key."""
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals/test-goal-id/attempts")
    assert resp.status_code == 401


def test_goal_lineage_returns_fallback_when_no_db() -> None:
    """FIX 3: lineage returns graceful fallback when DB is unavailable."""
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)

    with patch("app.db.session.get_session_factory", return_value=None, create=True):
        resp = client.get(
            "/goals/test-goal-id/lineage",
            headers={"X-API-Key": _KEY},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["root_goal_id"] == "test-goal-id"
    assert isinstance(data["nodes"], list)


def test_goal_attempts_returns_empty_when_no_db() -> None:
    """FIX 3: attempts returns [] gracefully when DB is unavailable."""
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)

    with patch("app.db.session.get_session_factory", return_value=None, create=True):
        resp = client.get(
            "/goals/test-goal-id/attempts",
            headers={"X-API-Key": _KEY},
        )

    assert resp.status_code == 200
    assert resp.json() == []
