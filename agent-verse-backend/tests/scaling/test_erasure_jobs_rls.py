"""Regression: data-subject-rights jobs actually run, under the right DB roles.

* GDPR tenant erasure: POST /enterprise/compliance/delete answered
  ``deletion_scheduled: true`` but nothing ever called
  ``execute_data_deletion_async`` — no task, no schedule.
* DPDP erasure: ``process_dpdp_erasures`` had no beat entry, and scanned the
  FORCE-RLS ``dpdp_erasure_requests`` table on the app session with no tenant
  context → the policy hid every row → 0 processed, forever.
* ``process_feedback_batch``: same RLS bug on ``goal_feedback``.

The fakes model RLS: the app-role recorder returns NO rows for a cross-tenant
scan (as Postgres does with the GUC unset); only the maintenance-role recorder
(``system_session``) sees them.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests._rls_recorder import RlsRecordingDb


def _system_rows(table: str, rows: list[Any]) -> Any:
    def _rows_for(sql: str, _p: dict[str, Any]) -> list[Any]:
        return rows if sql.startswith("SELECT") and table in sql else []

    return _rows_for


# ── beat schedule ─────────────────────────────────────────────────────────────


def test_erasure_tasks_are_scheduled_and_registered() -> None:
    import app.scaling.tasks  # noqa: F401  (registers tasks)
    from app.scaling.celery_app import celery_app

    scheduled = {e["task"] for e in celery_app.conf.beat_schedule.values()}
    for name in (
        "agentverse.process_dpdp_erasures",
        "agentverse.maintenance.process_tenant_erasures",
        "agentverse.maintenance.process_feedback_batch",
    ):
        assert name in scheduled, f"{name} has no beat schedule entry"
        assert name in celery_app.tasks, f"{name} is not a registered task"


# ── DPDP erasures ─────────────────────────────────────────────────────────────


def test_dpdp_erasure_scan_uses_maintenance_role_and_tenant_rls_per_request() -> None:
    from app.scaling.tasks import process_dpdp_erasures

    system_db = RlsRecordingDb(
        rows_for=_system_rows("dpdp_erasure_requests", [("req-1", "t1", "dp-1")])
    )
    app_db = RlsRecordingDb()  # RLS: a GUC-less scan here sees nothing
    orchestrator = MagicMock()
    orchestrator.execute_deletion = AsyncMock(
        return_value=MagicMock(suspended=False, verified=True, residue={})
    )
    with (
        patch("app.db.session.get_session_factory", return_value=app_db),
        patch("app.db.session.get_system_session_factory", return_value=system_db),
        patch("app.governance.audit_v3.AuditV3", return_value=MagicMock()),
        patch(
            "app.lifecycle.deletion_orchestrator.DeletionOrchestrator",
            return_value=orchestrator,
        ),
    ):
        result = process_dpdp_erasures.run()

    assert result["processed"] == 1, result
    orchestrator.execute_deletion.assert_awaited_once_with("t1", "dp-1")
    assert system_db.escalations >= 1  # scan + claim ran as the maintenance role
    (claim,) = list(system_db.touching("UPDATE dpdp_erasure_requests"))
    assert "'processing'" in claim.sql
    (done,) = app_db.touching("UPDATE dpdp_erasure_requests")
    assert done.tenant_guc == "t1" and done.params["st"] == "completed"
    assert app_db.escalations == 0  # tenant path never escalates


def test_dpdp_erasure_failure_returns_request_to_pending() -> None:
    from app.scaling.tasks import process_dpdp_erasures

    system_db = RlsRecordingDb(
        rows_for=_system_rows("dpdp_erasure_requests", [("req-1", "t1", "dp-1")])
    )
    app_db = RlsRecordingDb()
    orchestrator = MagicMock()
    orchestrator.execute_deletion = AsyncMock(side_effect=RuntimeError("boom"))
    with (
        patch("app.db.session.get_session_factory", return_value=app_db),
        patch("app.db.session.get_system_session_factory", return_value=system_db),
        patch("app.governance.audit_v3.AuditV3", return_value=MagicMock()),
        patch(
            "app.lifecycle.deletion_orchestrator.DeletionOrchestrator",
            return_value=orchestrator,
        ),
    ):
        result = process_dpdp_erasures.run()
    assert result["processed"] == 0 and result["failed"] == 1
    (upd,) = app_db.touching("UPDATE dpdp_erasure_requests")
    assert upd.params["st"] == "pending"


# ── feedback batch ────────────────────────────────────────────────────────────


def test_feedback_batch_scans_as_maintenance_role() -> None:
    from app.scaling.tasks import process_feedback_batch

    system_db = RlsRecordingDb(rows_for=_system_rows("goal_feedback", [("t1",), ("t2",)]))
    app_db = RlsRecordingDb()
    engine_svc = MagicMock()
    engine_svc.process_feedback_batch = AsyncMock(
        side_effect=[{"processed": 3, "actions_derived": 1}, {"processed": 2, "actions_derived": 0}]
    )
    with (
        patch("app.db.session.get_session_factory", return_value=app_db),
        patch("app.db.session.get_system_session_factory", return_value=system_db),
        # The old implementation built its own engine; hand it the RLS-blind app db.
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=MagicMock()),
        patch("sqlalchemy.ext.asyncio.async_sessionmaker", return_value=app_db),
        patch(
            "app.evals.self_improvement_engine.SelfImprovementEngine", return_value=engine_svc
        ),
    ):
        result = process_feedback_batch.run()

    assert result == {"processed": 5, "actions_derived": 1}
    assert system_db.escalations >= 1
    calls = engine_svc.process_feedback_batch.await_args_list
    assert [c.kwargs["tenant_id"] for c in calls] == ["t1", "t2"]
    assert all(c.kwargs["db_session_factory"] is app_db for c in calls)


# ── GDPR tenant erasure job ───────────────────────────────────────────────────


def _run_tenant_erasures(
    execute_result: dict[str, Any] | Exception,
) -> tuple[dict[str, Any], RlsRecordingDb, RlsRecordingDb, MagicMock]:
    import app.scaling.tasks as tasks

    system_db = RlsRecordingDb(rows_for=_system_rows("deleted_tenants", [("t1",)]))
    app_db = RlsRecordingDb(rows_for=_system_rows("api_keys", [("hash-1",)]))
    redis = MagicMock()
    mock = (
        AsyncMock(side_effect=execute_result)
        if isinstance(execute_result, Exception)
        else AsyncMock(return_value=execute_result)
    )
    with (
        patch("app.db.session.get_session_factory", return_value=app_db),
        patch("app.db.session.get_system_session_factory", return_value=system_db),
        patch("app.scaling.tasks._get_sync_redis", return_value=redis),
        patch(
            "app.enterprise.compliance.ComplianceController.execute_data_deletion_async", mock
        ),
    ):
        result = tasks.process_tenant_erasures.run()
    return result, system_db, app_db, redis


def _final_status(system_db: RlsRecordingDb) -> dict[str, Any]:
    updates = [s for s in system_db.touching("UPDATE deleted_tenants") if ":st" in s.sql]
    assert len(updates) == 1
    return updates[0].params


def test_tenant_erasure_job_runs_and_records_completion() -> None:
    result, system_db, app_db, redis = _run_tenant_erasures(
        {"complete": True, "total_rows_deleted": 7, "failed_tables": []}
    )
    assert result["completed"] == 1
    assert system_db.escalations >= 1  # scan/claim/record: maintenance role
    (keys,) = app_db.touching("FROM api_keys")
    assert keys.tenant_guc == "t1"  # per-tenant work under tenant RLS
    assert _final_status(system_db)["st"] == "completed"
    redis.delete.assert_called_once_with("tenant:t1", "api_key:hash-1")


def test_tenant_erasure_blocked_by_legal_hold_stays_on_hold() -> None:
    result, system_db, _app_db, redis = _run_tenant_erasures(
        {"blocked": "legal_hold", "complete": False}
    )
    assert result["on_hold"] == 1
    assert _final_status(system_db)["st"] == "on_hold"
    redis.delete.assert_not_called()


def test_tenant_erasure_failure_is_recorded_not_completed() -> None:
    result, system_db, _app_db, _redis = _run_tenant_erasures(RuntimeError("fk violation"))
    assert result["failed"] == 1
    params = _final_status(system_db)
    assert params["st"] == "failed" and "fk violation" in params["err"]


@pytest.mark.asyncio
async def test_execute_data_deletion_refuses_while_legal_hold_active() -> None:
    from app.enterprise.compliance import ComplianceController
    from app.tenancy.context import PlanTier, TenantContext

    db = RlsRecordingDb(rows_for=_system_rows("legal_holds", [(1,)]))
    result = await ComplianceController().execute_data_deletion_async(
        tenant_ctx=TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k"), db=db
    )
    assert result["blocked"] == "legal_hold"
    assert result["complete"] is False
    assert not [s for s in db.statements if s.sql.startswith("DELETE")]
    (hold,) = db.touching("FROM legal_holds")
    assert hold.tenant_guc == "t1"


# ── API: request is durable, status reflects the stored job ──────────────────


def _enterprise_app(controller: Any) -> FastAPI:
    from app.api.enterprise import router
    from app.tenancy.context import PlanTier, TenantContext

    app = FastAPI()
    app.include_router(router)
    app.state.compliance_controller = controller

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = TenantContext(
            tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k"
        )
        return await call_next(request)

    return app


def test_deletion_request_db_failure_is_503_not_scheduled() -> None:
    from app.enterprise.compliance import ComplianceController

    cc = ComplianceController()
    broken = MagicMock(side_effect=RuntimeError("db down"))
    cc.configure_services(db=broken)
    resp = TestClient(_enterprise_app(cc)).post("/enterprise/compliance/delete")
    assert resp.status_code == 503


def test_deletion_status_endpoint_reflects_stored_job() -> None:
    from app.enterprise.compliance import ComplianceController

    row = ("on_hold", None, None, 0, None, None, None)
    db = RlsRecordingDb(rows_for=_system_rows("deleted_tenants", [row]))
    cc = ComplianceController()
    cc.configure_services(db=db)
    client = TestClient(_enterprise_app(cc))
    posted = client.post("/enterprise/compliance/delete")
    assert posted.status_code == 202
    assert posted.json()["status"] == "on_hold"
    (insert,) = db.touching("INSERT INTO deleted_tenants")
    assert insert.tenant_guc == "t1"
    got = client.get("/enterprise/compliance/delete")
    assert got.status_code == 200
    assert got.json()["status"] == "on_hold" and got.json()["legal_hold"] is True
