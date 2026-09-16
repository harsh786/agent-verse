"""Fast-tier (no Docker/Postgres) unit coverage for ``PostgresWorkflowRunStore``.

A sibling suite already proves RLS enforcement end-to-end against a real
Postgres container (``pytest.mark.integration``), but that suite needs Docker
and is excluded from the fast tier. This file mocks the SQLAlchemy async
session so the store's SQL-building / branching logic (row mapping,
status-transition columns, cross-tenant maintenance helpers, not-found paths)
is exercised without any live infra.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.workflow.run_store import (
    PostgresWorkflowRunStore,
    _as_obj,
    _as_str,
    _duration_ms,
    _iso,
)
from app.workflow.state import StepStatus, WorkflowRunStatus

# NOTE: asyncio_mode = "auto" (pyproject.toml) auto-detects `async def` tests,
# so no explicit `pytestmark = pytest.mark.asyncio` is needed (and applying it
# to the sync helper tests below would trigger a pytest-asyncio warning that
# `filterwarnings = ["error"]` turns into a failure).


# ── Fakes ──────────────────────────────────────────────────────────────────────


class _Mappings:
    def __init__(self, first: Any = None, all_: list[Any] | None = None) -> None:
        self._first = first
        self._all = all_ or []

    def first(self) -> Any:
        return self._first

    def all(self) -> list[Any]:
        return self._all


class FakeResult:
    """Stands in for a SQLAlchemy ``CursorResult``."""

    def __init__(
        self,
        *,
        scalar: Any = None,
        first: Any = None,
        fetchall: list[Any] | None = None,
        mapping_first: Any = None,
        mapping_all: list[Any] | None = None,
        rowcount: int = 0,
    ) -> None:
        self._scalar = scalar
        self._first = first
        self._fetchall = fetchall or []
        self._mapping_first = mapping_first
        self._mapping_all = mapping_all
        self.rowcount = rowcount

    def scalar_one(self) -> Any:
        return self._scalar

    def first(self) -> Any:
        return self._first

    def fetchall(self) -> list[Any]:
        return self._fetchall

    def mappings(self) -> _Mappings:
        return _Mappings(self._mapping_first, self._mapping_all)


class FakeSession:
    """One ``async with self._db() as session:`` block's worth of state."""

    def __init__(self, results: list[FakeResult] | None = None) -> None:
        self._results = list(results or [])
        self.executed: list[tuple[str, Any]] = []
        self.committed = False

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        self.executed.append((str(stmt), params))
        if self._results:
            return self._results.pop(0)
        return FakeResult()

    async def commit(self) -> None:
        self.committed = True

    def begin(self) -> FakeSession:
        return self


class FakeDBFactory:
    """Callable ``db_factory`` that hands out a fresh ``FakeSession`` per call."""

    def __init__(self, batches: list[list[FakeResult]] | None = None) -> None:
        self._batches = list(batches or [])
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        results = self._batches.pop(0) if self._batches else []
        session = FakeSession(results)
        self.sessions.append(session)
        return session


# ── Pure helpers (no DB) ───────────────────────────────────────────────────────


def test_as_str_coerces_enum_and_passes_through_plain_str() -> None:
    assert _as_str(WorkflowRunStatus.COMPLETE) == "complete"
    assert _as_str("already-a-string") == "already-a-string"


def test_as_obj_parses_json_string_and_passes_through_dict() -> None:
    assert _as_obj('{"a": 1}') == {"a": 1}
    assert _as_obj({"a": 1}) == {"a": 1}
    assert _as_obj("not json") == "not json"  # falls back to raw string
    assert _as_obj(None) is None


def test_iso_handles_none_and_datetime_and_other() -> None:
    assert _iso(None) is None
    dt = datetime(2026, 1, 1, tzinfo=UTC)
    assert _iso(dt) == dt.isoformat()
    assert _iso("already-str") == "already-str"


def test_duration_ms_requires_both_datetimes() -> None:
    dt1 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    dt2 = datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)
    assert _duration_ms(dt1, dt2) == 1000.0
    assert _duration_ms(None, dt2) is None
    assert _duration_ms(dt1, None) is None


def test_row_to_run_defaults_missing_fields() -> None:
    row = {
        "id": "run-1",
        "workflow_id": None,
        "workflow_name": None,
        "status": "pending",
        "inputs": None,
        "outputs": None,
        "error": None,
        "started_at": None,
        "completed_at": None,
        "cost_usd": None,
    }
    out = PostgresWorkflowRunStore._row_to_run(row)
    assert out["run_id"] == "run-1"
    assert out["workflow_id"] == ""
    assert out["inputs"] == {}
    assert out["outputs"] == {}
    assert out["duration_ms"] is None
    assert out["step_count"] == 0
    assert out["cost_usd"] == 0
    assert out["tokens_used"] == 0


def test_row_to_step_maps_fields() -> None:
    row = {
        "step_id": "s1",
        "step_type": "tool",
        "status": "complete",
        "resolved_input": {"a": 1},
        "output": {"b": 2},
        "error": None,
        "started_at": None,
        "completed_at": None,
        "duration_ms": None,
    }
    out = PostgresWorkflowRunStore._row_to_step(row)
    assert out["step_id"] == "s1"
    assert out["input"] == {"a": 1}
    assert out["output"] == {"b": 2}
    assert out["duration_ms"] is None


def test_stats_row_coerces_types() -> None:
    row = {"total": 3, "completed": 2, "failed": 1, "avg_duration_s": None}
    out = PostgresWorkflowRunStore._stats_row(row)
    assert out == {"total": 3, "completed": 2, "failed": 1, "avg_duration_s": 0.0}


# ── create / get / list ────────────────────────────────────────────────────────


async def test_create_inserts_and_commits() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult()]])
    store = PostgresWorkflowRunStore(db)
    await store.create(
        run_id="run-1",
        workflow_id="wf-1",
        tenant_id="t1",
        trigger_payload={"x": 1},
        inputs={"y": 2},
        labels={"env": "prod"},
        is_test_run=True,
    )
    session = db.sessions[0]
    assert session.committed
    sql, params = session.executed[1]
    assert "INSERT INTO workflow_runs" in sql
    assert params["id"] == "run-1"
    assert params["is_test_run"] is True


async def test_get_found_and_not_found() -> None:
    row = {
        "id": "run-1",
        "workflow_id": "wf-1",
        "workflow_name": "WF",
        "status": "complete",
        "inputs": {},
        "outputs": {},
        "error": None,
        "started_at": None,
        "completed_at": None,
        "cost_usd": 1.5,
        "step_count": 2,
        "tokens_used": 10,
    }
    db = FakeDBFactory([[FakeResult(), FakeResult(mapping_first=row)]])
    store = PostgresWorkflowRunStore(db)
    got = await store.get("t1", "run-1")
    assert got is not None
    assert got["run_id"] == "run-1"
    assert got["cost_usd"] == 1.5

    db2 = FakeDBFactory([[FakeResult(), FakeResult(mapping_first=None)]])
    store2 = PostgresWorkflowRunStore(db2)
    assert await store2.get("t1", "missing") is None


async def test_list_applies_filters_and_returns_total() -> None:
    row = {
        "id": "run-1",
        "workflow_id": "wf-1",
        "workflow_name": "WF",
        "status": "complete",
        "inputs": {},
        "outputs": {},
        "error": None,
        "started_at": None,
        "completed_at": None,
        "cost_usd": 0,
        "step_count": 0,
        "tokens_used": 0,
    }
    db = FakeDBFactory(
        [[FakeResult(), FakeResult(scalar=1), FakeResult(mapping_all=[row])]]
    )
    store = PostgresWorkflowRunStore(db)
    items, total = await store.list("t1", workflow_id="wf-1", status="complete", limit=5, offset=0)
    assert total == 1
    assert items[0]["run_id"] == "run-1"
    session = db.sessions[0]
    # The COUNT and SELECT queries both carry the workflow_id/status clauses.
    assert any("r.workflow_id = CAST(:workflow_id AS uuid)" in sql for sql, _ in session.executed)
    assert any("r.status = :status" in sql for sql, _ in session.executed)


async def test_list_without_filters_uses_true_clause() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult(scalar=0), FakeResult(mapping_all=[])]])
    store = PostgresWorkflowRunStore(db)
    items, total = await store.list("t1")
    assert items == []
    assert total == 0


# ── update_status ──────────────────────────────────────────────────────────────


async def test_update_status_running_stamps_started_at() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult(rowcount=1)]])
    store = PostgresWorkflowRunStore(db)
    ok = await store.update_status("run-1", WorkflowRunStatus.RUNNING, tenant_id="t1")
    assert ok is True
    sql, params = db.sessions[0].executed[1]
    assert "started_at = COALESCE(started_at, NOW())" in sql
    assert "completed_at = NOW()" not in sql
    assert params["status"] == "running"


async def test_update_status_terminal_sets_completed_at_and_optional_fields() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult(rowcount=1)]])
    store = PostgresWorkflowRunStore(db)
    ok = await store.update_status(
        "run-1",
        WorkflowRunStatus.FAILED,
        tenant_id="t1",
        error="boom",
        error_step_id="s1",
        outputs={"partial": True},
        current_step_id="s2",
        cost_usd=0.5,
        tokens_used=42,
    )
    assert ok is True
    sql, params = db.sessions[0].executed[1]
    assert "completed_at = NOW()" in sql
    assert params["error"] == "boom"
    assert params["error_step_id"] == "s1"
    assert params["current_step_id"] == "s2"
    assert params["cost_usd"] == 0.5
    assert params["tokens_used"] == 42


async def test_update_status_no_rows_matched_returns_false() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult(rowcount=0)]])
    store = PostgresWorkflowRunStore(db)
    assert await store.update_status("missing", WorkflowRunStatus.COMPLETE, tenant_id="t1") is False


# ── get_workflow_id / get_status ───────────────────────────────────────────────


async def test_get_workflow_id_with_and_without_tenant() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult(first=("wf-1",))]])
    store = PostgresWorkflowRunStore(db)
    assert await store.get_workflow_id("run-1", tenant_id="t1") == "wf-1"

    db2 = FakeDBFactory([[FakeResult(first=("wf-2",))]])
    store2 = PostgresWorkflowRunStore(db2)
    assert await store2.get_workflow_id("run-1") == "wf-2"
    # No tenant_id → no set_config call, only the SELECT itself.
    assert len(db2.sessions[0].executed) == 1


async def test_get_workflow_id_missing_returns_empty_string() -> None:
    db = FakeDBFactory([[FakeResult(first=None)]])
    store = PostgresWorkflowRunStore(db)
    assert await store.get_workflow_id("run-x") == ""


async def test_get_status_found_and_missing() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult(first=("running",))]])
    store = PostgresWorkflowRunStore(db)
    assert await store.get_status("t1", "run-1") == "running"

    db2 = FakeDBFactory([[FakeResult(), FakeResult(first=None)]])
    store2 = PostgresWorkflowRunStore(db2)
    assert await store2.get_status("t1", "missing") is None


# ── get_step_result / get_definition ───────────────────────────────────────────


async def test_get_step_result_found_and_missing() -> None:
    row = {
        "step_id": "s1",
        "step_type": "tool",
        "status": "complete",
        "resolved_input": None,
        "output": None,
        "error": None,
        "started_at": None,
        "completed_at": None,
        "duration_ms": None,
    }
    db = FakeDBFactory([[FakeResult(), FakeResult(mapping_first=row)]])
    store = PostgresWorkflowRunStore(db)
    got = await store.get_step_result("t1", "run-1", "s1")
    assert got is not None
    assert got["step_id"] == "s1"

    db2 = FakeDBFactory([[FakeResult(), FakeResult(mapping_first=None)]])
    store2 = PostgresWorkflowRunStore(db2)
    assert await store2.get_step_result("t1", "run-1", "missing") is None


async def test_get_definition_returns_parsed_json() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult(first=('{"name": "wf"}',))]])
    store = PostgresWorkflowRunStore(db)
    dsl = await store.get_definition("wf-1", "t1")
    assert dsl == {"name": "wf"}


async def test_get_definition_missing_raises_keyerror() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult(first=None)]])
    store = PostgresWorkflowRunStore(db)
    with pytest.raises(KeyError):
        await store.get_definition("missing-wf", "t1")


# ── record_step_start / record_step_finish / list_step_results ────────────────


async def test_record_step_start_returns_uuid_and_commits() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult()]])
    store = PostgresWorkflowRunStore(db)
    result_id = await store.record_step_start(
        run_id="run-1", tenant_id="t1", step_id="s1", step_type="tool", step_name="Do thing"
    )
    assert isinstance(result_id, str) and len(result_id) > 0
    assert db.sessions[0].committed


async def test_record_step_finish_updates_and_reports_rowcount() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult(rowcount=1)]])
    store = PostgresWorkflowRunStore(db)
    ok = await store.record_step_finish(
        run_id="run-1", tenant_id="t1", step_id="s1", status=StepStatus.COMPLETE, output={"n": 1}
    )
    assert ok is True

    db2 = FakeDBFactory([[FakeResult(), FakeResult(rowcount=0)]])
    store2 = PostgresWorkflowRunStore(db2)
    assert (
        await store2.record_step_finish(
            run_id="run-1", tenant_id="t1", step_id="missing", status=StepStatus.FAILED, error="x"
        )
        is False
    )


async def test_list_step_results_maps_all_rows() -> None:
    row = {
        "step_id": "s1",
        "step_type": "tool",
        "status": "complete",
        "resolved_input": None,
        "output": None,
        "error": None,
        "started_at": None,
        "completed_at": None,
        "duration_ms": 12.5,
    }
    db = FakeDBFactory([[FakeResult(), FakeResult(mapping_all=[row, row])]])
    store = PostgresWorkflowRunStore(db)
    steps = await store.list_step_results("t1", "run-1")
    assert len(steps) == 2
    assert steps[0]["duration_ms"] == 12.5


# ── Maintenance (cross-tenant) ──────────────────────────────────────────────────


async def test_get_retryable_webhooks_maps_rows() -> None:
    row = {
        "id": "evt-1",
        "tenant_id": "t1",
        "workflow_id": "wf-1",
        "payload": '{"hello": "world"}',
    }
    db = FakeDBFactory([[FakeResult(), FakeResult(mapping_all=[row])]])
    store = PostgresWorkflowRunStore(db)
    events = await store.get_retryable_webhooks(max_attempts=5)
    assert events[0]["id"] == "evt-1"
    assert events[0]["payload"] == {"hello": "world"}


async def test_get_retryable_webhooks_empty() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult(mapping_all=[])]])
    store = PostgresWorkflowRunStore(db)
    assert await store.get_retryable_webhooks() == []


async def test_delete_expired_runs_returns_rowcount() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult(rowcount=7)]])
    store = PostgresWorkflowRunStore(db)
    assert await store.delete_expired_runs() == 7


async def test_delete_expired_runs_none_deleted() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult(rowcount=None)]])
    store = PostgresWorkflowRunStore(db)
    assert await store.delete_expired_runs() == 0


# ── Versions ────────────────────────────────────────────────────────────────────


async def test_list_versions_maps_rows() -> None:
    row = {
        "id": "v1",
        "version": "1.0.0",
        "change_summary": "init",
        "published_by": "user-1",
        "published_at": None,
    }
    db = FakeDBFactory([[FakeResult(), FakeResult(mapping_all=[row])]])
    store = PostgresWorkflowRunStore(db)
    versions = await store.list_versions("t1", "wf-1")
    assert versions[0]["version"] == "1.0.0"
    assert versions[0]["published_by"] == "user-1"


async def test_get_definition_version_found_and_missing() -> None:
    row = {"version": "1.0.0", "definition_yaml": "name: x", "definition_json": {"a": 1}}
    db = FakeDBFactory([[FakeResult(), FakeResult(mapping_first=row)]])
    store = PostgresWorkflowRunStore(db)
    snap = await store.get_definition_version("t1", "wf-1", "1.0.0")
    assert snap is not None
    assert snap["definition_json"] == {"a": 1}

    db2 = FakeDBFactory([[FakeResult(), FakeResult(mapping_first=None)]])
    store2 = PostgresWorkflowRunStore(db2)
    assert await store2.get_definition_version("t1", "wf-1", "9.9.9") is None


# ── Permissions ─────────────────────────────────────────────────────────────────


async def test_get_permissions_maps_rows() -> None:
    row = {
        "id": "p1",
        "subject_type": "user",
        "subject_id": "u1",
        "permission": "editor",
        "granted_by": "admin",
        "granted_at": None,
    }
    db = FakeDBFactory([[FakeResult(), FakeResult(mapping_all=[row])]])
    store = PostgresWorkflowRunStore(db)
    perms = await store.get_permissions("t1", "wf-1")
    assert perms[0]["subject_id"] == "u1"


async def test_add_permission_returns_row() -> None:
    row = {
        "id": "p1",
        "subject_type": "user",
        "subject_id": "u1",
        "permission": "editor",
        "granted_at": None,
    }
    db = FakeDBFactory([[FakeResult(), FakeResult(mapping_first=row)]])
    store = PostgresWorkflowRunStore(db)
    created = await store.add_permission(
        "t1", "wf-1", subject_type="user", subject_id="u1", permission="editor"
    )
    assert created["id"] == "p1"
    assert created["subject_id"] == "u1"


async def test_remove_permission_true_and_false() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult(rowcount=1)]])
    store = PostgresWorkflowRunStore(db)
    assert await store.remove_permission("t1", "wf-1", "p1") is True

    db2 = FakeDBFactory([[FakeResult(), FakeResult(rowcount=0)]])
    store2 = PostgresWorkflowRunStore(db2)
    assert await store2.remove_permission("t1", "wf-1", "missing") is False


# ── Analytics ────────────────────────────────────────────────────────────────────


async def test_workflow_run_stats_and_aggregate_run_stats() -> None:
    row = {"total": 5, "completed": 4, "failed": 1, "avg_duration_s": 1.25}
    db = FakeDBFactory([[FakeResult(), FakeResult(mapping_first=row)]])
    store = PostgresWorkflowRunStore(db)
    stats = await store.workflow_run_stats("t1", "wf-1", days=7)
    assert stats["total"] == 5
    assert stats["avg_duration_s"] == 1.25

    db2 = FakeDBFactory([[FakeResult(), FakeResult(mapping_first=row)]])
    store2 = PostgresWorkflowRunStore(db2)
    agg = await store2.aggregate_run_stats("t1", days=1)
    assert agg["completed"] == 4


# ── Webhook events ────────────────────────────────────────────────────────────────


async def test_list_webhook_events_maps_rows_and_total() -> None:
    row = {
        "id": "evt-1",
        "webhook_token": "tok-1",
        "status": "completed",
        "attempts": 2,
        "last_error": None,
        "run_id": "run-1",
        "received_at": None,
        "last_attempted_at": None,
        "completed_at": None,
    }
    db = FakeDBFactory([[FakeResult(), FakeResult(scalar=1), FakeResult(mapping_all=[row])]])
    store = PostgresWorkflowRunStore(db)
    events, total = await store.list_webhook_events("t1", "wf-1", limit=10, offset=0)
    assert total == 1
    assert events[0]["webhook_token"] == "tok-1"
    assert events[0]["run_id"] == "run-1"
