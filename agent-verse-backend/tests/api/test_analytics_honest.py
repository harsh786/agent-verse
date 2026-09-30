"""Regression: analytics swallowed failures into zeros and read evals off-RLS.

* /analytics/goals and /analytics/costs turned any aggregator error into
  all-zero metrics — indistinguishable from "no goals";
* /analytics/evals queried the RLS-scoped ``evaluations`` table without the
  tenant GUC (always 0 rows under the app role) and ``pass``-ed on errors.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.analytics import router
from app.tenancy.context import PlanTier, TenantContext
from tests._rls_recorder import RlsRecordingDb

CTX = TenantContext(tenant_id="t-an", plan=PlanTier.FREE, api_key_id="k")


def _client(db: Any = None) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _t(request: Request, call_next: Any) -> Any:
        request.state.tenant = CTX
        return await call_next(request)

    app.include_router(router)
    app.state.db_session_factory = db
    app.state.goal_service = None
    return TestClient(app, raise_server_exceptions=False)


def test_goal_analytics_failure_is_503_not_zeros() -> None:
    with patch(
        "app.analytics.aggregator.GoalAnalyticsAggregator.goal_metrics",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        assert _client().get("/analytics/goals").status_code == 503


def test_eval_analytics_query_runs_under_the_tenant_guc() -> None:
    db = RlsRecordingDb(rows_for=lambda sql, p: [(2, 0.9, 0.8, 0.7, 1.0, 0.6, 1)]
                        if "COUNT(*) as total" in sql and "GROUP BY" not in sql else [])
    r = _client(db).get("/analytics/evals")
    assert r.status_code == 200, r.text
    assert r.json()["total_evals"] == 2
    (stmt,) = [s for s in db.touching("FROM evaluations") if "GROUP BY" not in s.sql]
    assert stmt.tenant_guc == CTX.tenant_id


def test_eval_analytics_failure_is_503() -> None:
    def _boom() -> Any:
        raise RuntimeError("db down")

    assert _client(_boom).get("/analytics/evals").status_code == 503


# ── ENT-01: no cross-tenant fallback; a DB error is a 503 on every endpoint ──


class _OtherTenantsGoal:
    def __init__(self) -> None:
        self.tenant_id = "someone-else"
        self.status = "complete"
        self.agent_id = "their-agent"
        self.cost_usd = 9.0
        self.created_at = datetime.now(UTC).isoformat()
        self.completed_at = None
        self.eval_score = None
        self.events = [{"type": "tool_call_complete", "tool": "their:tool"}]


class _GoalServiceWithOtherTenants:
    def __init__(self) -> None:
        self._goals = {"x": _OtherTenantsGoal()}

    async def get_metrics(self, tenant_ctx: Any) -> dict[str, Any]:
        return {"cost_today_usd": 0.0}


def _client_with_goals(db: Any) -> TestClient:
    client = _client(db)
    client.app.state.goal_service = _GoalServiceWithOtherTenants()  # type: ignore[attr-defined]
    return client


def test_empty_tenant_db_never_shows_other_tenants_goals() -> None:
    db = RlsRecordingDb(rows_for=lambda sql, p: [])
    client = _client_with_goals(db)
    goals = client.get("/analytics/goals")
    assert goals.status_code == 200, goals.text
    assert goals.json()["total"] == 0
    assert client.get("/analytics/tools").json()["tools"] == []
    assert client.get("/analytics/agents").json()["agents"] == []
    costs = client.get("/analytics/costs").json()
    assert costs["total_cost_usd"] == 0
    assert costs["cost_by_day"] == []


def test_every_analytics_endpoint_is_503_when_the_db_fails() -> None:
    def _boom() -> Any:
        raise RuntimeError("db down")

    client = _client_with_goals(_boom)
    for path in ("/analytics/goals", "/analytics/tools", "/analytics/costs", "/analytics/agents"):
        assert client.get(path).status_code == 503, path
