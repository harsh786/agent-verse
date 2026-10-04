"""a09-F212-01: the sync GDPR export is 'ready' only when it is complete.

``request_data_export`` set ``status = "ready"`` before collecting anything; a
goals DB error was only logged (export still 'ready' with ``goals=[]``), audit
entries were capped at 100, agents/schedules came from this replica's in-memory
cache, and ``knowledge_collections`` was hardcoded ``[]``. A GDPR Art. 15/20
export could be reported complete while missing data. Now every section is read
(from Postgres when configured, bounded), any failure or an over-limit section
marks the export ``failed`` with a reason, and a failed export never downloads.
"""

from __future__ import annotations

import warnings
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from app.enterprise import compliance as compliance_mod
    from app.enterprise.compliance import ComplianceController

from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-gdpr-complete", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        self._db.log.append((sql, dict(params or {})))
        for table, rows in self._db.tables.items():
            if f"FROM {table} " in sql or sql.rstrip().endswith(f"FROM {table}"):
                if table in self._db.fail:
                    raise RuntimeError(f"{table} unavailable")
                return _Result(rows[: int((params or {}).get("lim", len(rows)))])
        return _Result([])

    @asynccontextmanager
    async def begin(self) -> Any:
        yield self

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeDb:
    def __init__(
        self, tables: dict[str, list[tuple[Any, ...]]], fail: set[str] | None = None
    ) -> None:
        self.tables = tables
        self.fail = set(fail or ())
        self.log: list[tuple[str, dict[str, Any]]] = []

    def __call__(self) -> _Session:
        return _Session(self)


_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _tables(goals: int = 1, audit: int = 1) -> dict[str, list[tuple[Any, ...]]]:
    return {
        "goals": [(f"g{i}", "text", "complete", _NOW) for i in range(goals)],
        "audit_log": [(f"e{i}", "g0", "tool", "success", _NOW) for i in range(audit)],
        "agents": [("a1", "Agent", True, _NOW)],
        "schedules": [("s1", "a1", "cron", "0 * * * *", "", _NOW)],
        "knowledge_collections": [("c1", "Docs", "desc", 3, _NOW)],
    }


def _controller(db: _FakeDb) -> ComplianceController:
    cc = ComplianceController()
    cc.configure_services(db=db)
    return cc


@pytest.mark.asyncio
async def test_db_export_reads_every_section_bounded_and_tenant_scoped() -> None:
    db = _FakeDb(_tables(audit=150))
    req = await _controller(db).request_data_export(tenant_ctx=T)

    assert req.status == "ready", req.payload
    data = req.payload["data"]
    assert len(data["audit_entries"]) == 150  # no silent 100-entry cap
    assert [a["agent_id"] for a in data["agents"]] == ["a1"]
    assert [s["schedule_id"] for s in data["schedules"]] == ["s1"]
    assert [c["collection_id"] for c in data["knowledge_collections"]] == ["c1"]
    selects = [(sql, p) for sql, p in db.log if " FROM " in sql.upper()]
    assert len(selects) == 5
    # Every data read runs under the tenant's RLS GUC as well.
    assert sum("set_config('app.tenant_id', :tid" in sql for sql, _ in db.log) >= 5
    for sql, params in selects:
        assert "tenant_id = :tid" in sql
        assert params["tid"] == T.tenant_id
        assert "LIMIT :lim" in sql


@pytest.mark.asyncio
async def test_goal_db_error_marks_the_export_failed_not_ready() -> None:
    db = _FakeDb(_tables(), fail={"goals"})
    req = await _controller(db).request_data_export(tenant_ctx=T)

    assert req.status == "failed"
    assert "goals" in req.payload["failed_sections"]
    assert "goals" in req.payload["error"]
    assert "data" not in req.payload  # no partial data handed out as an export


@pytest.mark.asyncio
async def test_section_over_the_sync_limit_fails_with_a_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compliance_mod, "SYNC_EXPORT_MAX_ROWS", 2)
    db = _FakeDb(_tables(goals=3))
    req = await _controller(db).request_data_export(tenant_ctx=T)

    assert req.status == "failed"
    assert "too_large" in req.payload["failed_sections"]["goals"]
    # The query asked for one row past the limit, never the whole table.
    goal_sql = [p for sql, p in db.log if "FROM goals" in sql]
    assert goal_sql and goal_sql[0]["lim"] == 3


@pytest.mark.asyncio
async def test_in_memory_audit_error_marks_the_export_failed() -> None:
    cc = ComplianceController()
    audit = MagicMock()
    audit.query = MagicMock(side_effect=RuntimeError("audit store down"))
    cc.configure_services(audit_log=audit)

    req = await cc.request_data_export(tenant_ctx=T)
    assert req.status == "failed"
    assert "audit_entries" in req.payload["failed_sections"]


@pytest.mark.asyncio
async def test_in_memory_knowledge_collections_are_exported() -> None:
    cc = ComplianceController()
    ks = MagicMock()
    ks.list_collections = MagicMock(
        return_value=[SimpleNamespace(collection_id="c9", name="KB", description="", document_count=1)]
    )
    cc.configure_services(knowledge_store=ks)

    req = await cc.request_data_export(tenant_ctx=T)
    assert req.status == "ready"
    assert [c["collection_id"] for c in req.payload["data"]["knowledge_collections"]] == ["c9"]


def test_failed_export_download_is_refused_with_the_reason() -> None:
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from app.api.enterprise import router

    app = FastAPI()
    cc = _controller(_FakeDb(_tables(), fail={"goals"}))
    app.state.compliance_controller = cc
    app.state.compliance = cc

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        request.state.tenant = T
        return await call_next(request)

    app.include_router(router)
    client = TestClient(app)
    started = client.get("/enterprise/compliance/export")
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["status"] == "failed"
    assert "goals" in body["error"]

    dl = client.get(f"/enterprise/compliance/export/{body['request_id']}/download")
    assert dl.status_code == 409, dl.text
