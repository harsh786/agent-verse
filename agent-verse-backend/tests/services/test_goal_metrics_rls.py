"""a09-F223-02: GoalService.get_metrics reads ``goals`` under the tenant GUC.

``goals`` is FORCE ROW LEVEL SECURITY and the app runs as a NOBYPASSRLS role,
so the aggregate it ran with no ``app.tenant_id`` set matched zero rows and
GET /goals/metrics and /goals/cost-metrics showed 0 counts and success rate.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext
from tests._rls_recorder import RlsRecordingDb

CTX = TenantContext(tenant_id="t-metrics-rls", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _rows(sql: str, params: dict[str, Any]) -> list[Any]:
    if "FROM goals" in sql:
        # completed, failed, active, cancelled, completed_today, avg_ms, submitted_today
        return [(8, 2, 1, 0, 3, 1500.0, 4)]
    return []


@pytest.mark.asyncio
async def test_metrics_aggregate_runs_under_the_tenant_guc_with_explicit_predicate() -> None:
    db = RlsRecordingDb(rows_for=_rows)
    svc = GoalService()
    svc._db = db

    metrics = await svc.get_metrics(CTX)

    (stmt,) = db.touching("FROM goals")
    assert stmt.tenant_guc == CTX.tenant_id
    assert "tenant_id = :tid" in stmt.sql
    assert stmt.params["tid"] == CTX.tenant_id
    assert db.escalations == 0
    assert metrics["completed_goals"] == 8
    assert metrics["success_rate"] == 0.8
