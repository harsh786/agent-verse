"""Regression: analytics swallowed failures into zeros and read evals off-RLS.

* /analytics/goals and /analytics/costs turned any aggregator error into
  all-zero metrics — indistinguishable from "no goals";
* /analytics/evals queried the RLS-scoped ``evaluations`` table without the
  tenant GUC (always 0 rows under the app role) and ``pass``-ed on errors.
"""

from __future__ import annotations

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
