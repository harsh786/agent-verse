"""Unit guard: compliance/enterprise tables are only touched inside a tenant RLS scope.

``consent_records``, ``gdpr_export_jobs``, ``enterprise_contracts`` and
``deleted_tenants`` are tenant-isolated by row level security (ENABLE + FORCE +
a ``tenant_id = current_setting('app.tenant_id', true)`` policy). The API runs as
a NOBYPASSRLS role, so every statement against them must run in a transaction
that has ``app.tenant_id`` set to the calling tenant — otherwise reads match zero
rows and writes are rejected (and, in these handlers, the error was swallowed and
the endpoint still reported success).

These tests drive each request path with a recording session and assert:
  * the statement touching the table ran AFTER ``set_config('app.tenant_id', <caller>)``
    and before the GUC was reset, inside ``session.begin()``;
  * the statement still carries its own ``tenant_id`` predicate/value
    (defense in depth);
  * no request path escalates to ``row_security = off`` (the maintenance role's
    system_session) — that would be a privilege escalation.

The real-Postgres counterpart (policies applied, NOBYPASSRLS role) is
``tests/enterprise/test_compliance_tables_rls_integration.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.enterprise import compliance_router
from app.api.enterprise import router as enterprise_router
from app.enterprise.compliance import ComplianceController
from app.enterprise.compliance_v2 import ComplianceChecker
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

TENANT = "tenant-rls-a"
_CTX = TenantContext(
    tenant_id=TENANT, plan=PlanTier.ENTERPRISE, api_key_id="k-rls", roles=("admin",)
)
_KEY = "av_test_rls_compliance"
_HDR = {"X-API-Key": _KEY}


@dataclass
class _Result:
    rows: list[Any] = field(default_factory=list)
    scalar_value: Any = 0
    rowcount: int = 0

    def fetchone(self) -> Any:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[Any]:
        return list(self.rows)

    def scalar(self) -> Any:
        return self.scalar_value

    def first(self) -> Any:
        return self.rows[0] if self.rows else None


class _Recorder:
    """Session factory whose sessions log every statement with the live GUC state."""

    def __init__(self, results: dict[str, _Result] | None = None) -> None:
        self.log: list[dict[str, Any]] = []
        self._results = results or {}

    def __call__(self) -> _Session:
        return _Session(self)


class _Begin:
    def __init__(self, session: _Session) -> None:
        self._s = session

    async def __aenter__(self) -> None:
        self._s.in_tx = True

    async def __aexit__(self, *exc: object) -> None:
        self._s.in_tx = False


class _Session:
    def __init__(self, rec: _Recorder) -> None:
        self._rec = rec
        self.guc: str | None = None
        self.in_tx = False

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def begin(self) -> _Begin:
        return _Begin(self)

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        params = params or {}
        if "set_config('app.tenant_id'" in sql:
            self.guc = params.get("tid") or None
            return _Result()
        self._rec.log.append(
            {"sql": sql, "params": params, "guc": self.guc, "in_tx": self.in_tx}
        )
        for needle, result in self._rec._results.items():
            if needle in sql:
                return result
        return _Result()


def _stmts(rec: _Recorder, needle: str) -> list[dict[str, Any]]:
    found = [e for e in rec.log if needle in e["sql"]]
    assert found, f"no statement containing {needle!r} ran; log={[e['sql'] for e in rec.log]}"
    return found


def _assert_tenant_scoped(entry: dict[str, Any], *, tx: bool = True) -> None:
    assert entry["guc"] == TENANT, f"ran without the tenant GUC: {entry['sql']}"
    if tx:
        assert entry["in_tx"], f"ran outside session.begin(): {entry['sql']}"
    assert entry["params"].get("tid") == TENANT, "missing explicit tenant predicate/value"
    assert "row_security" not in entry["sql"]


def _app(rec: _Recorder) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(enterprise_router)
    app.include_router(compliance_router)
    app.state.db_session_factory = rec
    app.state.compliance_controller = ComplianceController()
    return app


# ── gdpr_export_jobs ──────────────────────────────────────────────────────────


def test_start_gdpr_export_inserts_job_under_tenant_guc() -> None:
    rec = _Recorder()
    client = TestClient(_app(rec), raise_server_exceptions=False)
    with patch("app.scaling.tasks.run_gdpr_export") as task:
        resp = client.post("/compliance/export/start", headers=_HDR)
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    (insert,) = _stmts(rec, "INSERT INTO gdpr_export_jobs")
    _assert_tenant_scoped(insert)
    assert insert["params"]["id"] == job_id
    task.delay.assert_called_once_with(job_id, TENANT)


def test_gdpr_export_status_reads_job_under_tenant_guc() -> None:
    rec = _Recorder({"FROM gdpr_export_jobs": _Result(rows=[("complete", None, "/dl", None)])})
    client = TestClient(_app(rec), raise_server_exceptions=False)
    resp = client.get("/compliance/export/jobs/job-9", headers=_HDR)
    assert resp.status_code == 200
    assert resp.json()["status"] == "complete"
    (select,) = _stmts(rec, "FROM gdpr_export_jobs")
    _assert_tenant_scoped(select)
    assert "tenant_id = :tid" in select["sql"]


# ── consent_records ───────────────────────────────────────────────────────────


def test_record_and_revoke_consent_run_under_tenant_guc() -> None:
    rec = _Recorder({"UPDATE consent_records": _Result(rowcount=1)})
    client = TestClient(_app(rec), raise_server_exceptions=False)
    assert (
        client.post("/compliance/consent", json={"purpose": "analytics"}, headers=_HDR)
    ).status_code == 200
    assert client.delete("/compliance/consent/analytics", headers=_HDR).status_code == 200
    (insert,) = _stmts(rec, "INSERT INTO consent_records")
    (update,) = _stmts(rec, "UPDATE consent_records")
    _assert_tenant_scoped(insert)
    _assert_tenant_scoped(update)
    assert "tenant_id = :tid" in update["sql"]


# ── enterprise_contracts ──────────────────────────────────────────────────────


def test_sign_and_list_contracts_run_under_tenant_guc() -> None:
    from datetime import UTC, datetime

    rec = _Recorder(
        {"INSERT INTO enterprise_contracts": _Result(rows=[(datetime(2026, 1, 1, tzinfo=UTC),)])}
    )
    client = TestClient(_app(rec), raise_server_exceptions=False)
    signed = client.post(
        "/enterprise/contracts/dpa/sign",
        json={"signer_name": "Ada", "signer_email": "ada@example.test"},
        headers=_HDR,
    )
    assert signed.status_code == 201
    assert signed.json()["signed_at"] == "2026-01-01T00:00:00+00:00"
    assert client.get("/enterprise/contracts", headers=_HDR).status_code == 200
    (insert,) = _stmts(rec, "INSERT INTO enterprise_contracts")
    (select,) = _stmts(rec, "FROM enterprise_contracts")
    _assert_tenant_scoped(insert)
    _assert_tenant_scoped(select)
    assert "tenant_id = :tid" in select["sql"]


# ── deleted_tenants + GDPR export via ComplianceController ────────────────────


@pytest.mark.asyncio
async def test_erasure_request_records_deleted_tenant_under_own_guc() -> None:
    rec = _Recorder(
        {"FROM deleted_tenants": _Result(rows=[("pending", None, None, 0, None, None, None)])}
    )
    cc = ComplianceController()
    cc.configure_services(db=rec)
    result = await cc.request_data_deletion(tenant_ctx=_CTX)
    assert result["deletion_scheduled"] is True
    (insert,) = _stmts(rec, "INSERT INTO deleted_tenants")
    _assert_tenant_scoped(insert)


@pytest.mark.asyncio
async def test_erasure_execution_checks_legal_hold_and_keeps_job_row() -> None:
    """The legal-hold gate reads legal_holds under the tenant's own GUC, and the
    deleted_tenants job row is kept (it records the job's status/result)."""
    rec = _Recorder()
    cc = ComplianceController()
    await cc.execute_data_deletion_async(tenant_ctx=_CTX, db=_NestedRecorder(rec))
    (hold,) = _stmts(rec, "FROM legal_holds")
    _assert_tenant_scoped(hold)
    assert not [e for e in rec.log if "DELETE FROM deleted_tenants" in e["sql"]]


@pytest.mark.asyncio
async def test_sync_export_reads_goals_under_tenant_guc() -> None:
    """The synchronous GDPR export read `goals` (FORCE RLS) with no GUC — under the
    API's role it silently exported zero goals."""
    rec = _Recorder({"FROM goals": _Result(rows=[("g1", "text", "complete", None)])})
    goal_service = MagicMock()
    goal_service._db_session_factory = rec
    cc = ComplianceController()
    cc.configure_services(goal_service=goal_service)
    req = await cc.request_data_export(tenant_ctx=_CTX)
    (select,) = _stmts(rec, "FROM goals")
    _assert_tenant_scoped(select)
    assert [g["goal_id"] for g in req.payload["data"]["goals"]] == ["g1"]


class _NestedRecorder(_Recorder):
    """Recorder whose sessions also support begin_nested() (SAVEPOINT per table)."""

    def __init__(self, inner: _Recorder) -> None:
        super().__init__()
        self.log = inner.log

    def __call__(self) -> _Session:
        s = _Session(self)
        s.begin_nested = lambda: _Begin(s)  # type: ignore[attr-defined]
        return s


# ── compliance_v2 reads ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_gdpr_counts_jobs_the_export_worker_marks_complete() -> None:
    """run_gdpr_export marks finished jobs status='complete'; the control only ever
    matched 'completed', so data portability could never pass."""
    rec = _Recorder({"FROM gdpr_export_jobs": _Result(scalar_value=2)})
    report = await ComplianceChecker(db_factory=rec).check_gdpr(TENANT)
    assert report["controls"]["data_portability"]["pass"] is True
    (select,) = _stmts(rec, "FROM gdpr_export_jobs")
    assert "'complete'" in select["sql"]
    _assert_tenant_scoped(select, tx=False)
    for table in ("FROM consent_records", "FROM enterprise_contracts"):
        for entry in _stmts(rec, table):
            _assert_tenant_scoped(entry, tx=False)


@pytest.mark.asyncio
async def test_check_soc2_reads_certifications_under_tenant_guc() -> None:
    rec = _Recorder({"FROM compliance_certifications": _Result(rows=[("active", None, "x")])})
    report = await ComplianceChecker(db_factory=rec).check_soc2(TENANT)
    assert report["controls"]["certification_on_file"]["pass"] is True
    (select,) = _stmts(rec, "FROM compliance_certifications")
    _assert_tenant_scoped(select, tx=False)


def test_non_admin_cannot_sign_a_contract() -> None:
    """Regression: any authenticated key could sign a BAA/DPA for the tenant."""
    viewer = TenantContext(
        tenant_id=TENANT, plan=PlanTier.ENTERPRISE, api_key_id="k-v", roles=("operator",)
    )
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return viewer if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(enterprise_router)
    rec = _Recorder()
    app.state.db_session_factory = rec
    r = TestClient(app, raise_server_exceptions=False).post(
        "/enterprise/contracts/dpa/sign",
        json={"signer_name": "Eve", "signer_email": "eve@example.test"},
        headers=_HDR,
    )
    assert r.status_code == 403
    assert not any("INSERT INTO enterprise_contracts" in e["sql"] for e in rec.log)
