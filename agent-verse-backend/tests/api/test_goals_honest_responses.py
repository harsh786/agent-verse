"""Regression: goal sub-endpoints that reported success they did not have.

* ghost-run answered 202 when EVERY strategy failed, leaking str(exc);
* batch status mapped any lookup error to ``not_found``, which counted toward
  ``all_complete=true``;
* feedback accepted any goal id (no existence / tenant check);
* two handlers were registered for GET /goals/{id}/explain; the second — the
  shape GoalExplainPanel reads — was unreachable.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.goals import router as goals_router
from app.core.errors import NotFoundError
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="t-honest", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_KEY = "ak_test_honest_goals"
H = {"X-API-Key": _KEY}


def _client(svc: Any) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(goals_router)
    app.state.goal_service = svc
    return TestClient(app, raise_server_exceptions=False)


def test_ghost_run_all_strategies_failed_is_503_without_leaking() -> None:
    svc = AsyncMock()
    svc.submit_goal.side_effect = RuntimeError("postgres://user:pw@db refused")
    r = _client(svc).post("/goals/ghost-run", json={"goal": "compare"}, headers=H)
    assert r.status_code == 503
    assert "pw@db" not in r.text


def test_ghost_run_partial_success_is_still_202() -> None:
    svc = AsyncMock()
    svc.submit_goal.side_effect = [{"goal_id": "g1"}, RuntimeError("x"), {"goal_id": "g3"}]
    r = _client(svc).post("/goals/ghost-run", json={"goal": "compare"}, headers=H)
    assert r.status_code == 202
    body = r.json()
    assert sorted(body["goal_ids"].values()) == ["g1", "g3"]
    assert any(s["error"] == "RuntimeError" for s in body["strategies"])


def test_batch_status_lookup_error_is_unknown_not_complete() -> None:
    svc = AsyncMock()
    svc.get_goal.side_effect = RuntimeError("db down")
    r = _client(svc).get("/goals/batch/g1/status", headers=H)
    body = r.json()
    assert body["goals"][0]["status"] == "unknown"
    assert body["all_complete"] is False


def test_feedback_on_unknown_goal_is_404() -> None:
    svc = AsyncMock()
    svc.get_goal.side_effect = NotFoundError("Goal not found: nope")
    r = _client(svc).post("/goals/nope/feedback", json={"rating": 1}, headers=H)
    assert r.status_code == 404


def test_explain_serves_the_decision_trace_fields() -> None:
    svc = AsyncMock()
    svc.get_goal.return_value = {
        "status": "complete",
        "plan": ["a", "b"],
        "execution_context": {
            "decision_traces": [{"action": "search"}],
            "model_selections": {"planner": "m1"},
            "runtime_profile": {"primary_strategy": "react"},
        },
    }
    body = _client(svc).get("/goals/g1/explain", headers=H).json()
    assert body["decision_traces"] == [{"action": "search"}]
    assert body["model_selections"] == {"planner": "m1"}
    assert body["plan"] == ["a", "b"]
    assert body["selected_strategy"] == "react"


def test_explain_is_registered_once() -> None:
    paths = [
        (getattr(r, "path", ""), tuple(sorted(getattr(r, "methods", ()) or ())))
        for r in goals_router.routes
    ]
    assert paths.count(("/goals/{goal_id}/explain", ("GET",))) == 1
