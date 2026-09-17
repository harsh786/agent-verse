"""Fast-tier (no Docker/Postgres) unit coverage for ``PostgresWorkflowApprovalStore``.

A sibling suite (``tests/workflow/test_approval_store.py``) proves RLS enforcement
end-to-end against a real Postgres container (``pytest.mark.integration``), but
that suite needs Docker and is excluded from the fast tier. This file mocks the
SQLAlchemy async session (mirroring ``tests/workflow/test_run_store_mocked.py``)
so the store's SQL-building, (de)serialization, and branching logic is exercised
without any live infra.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from app.workflow.approval_store import PostgresWorkflowApprovalStore
from app.workflow.hitl_extension import WorkflowHITLRequest

# asyncio_mode = "auto" (pyproject.toml) auto-detects `async def` tests.


# ── Fakes ──────────────────────────────────────────────────────────────────────


class FakeResult:
    def __init__(self, *, first: Any = None, all_: list[Any] | None = None) -> None:
        self._first = first
        self._all = all_ or []

    def first(self) -> Any:
        return self._first

    def all(self) -> list[Any]:
        return self._all


class FakeSession:
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
        return self._results.pop(0) if self._results else FakeResult()

    async def commit(self) -> None:
        self.committed = True


class FakeDBFactory:
    def __init__(self, batches: list[list[FakeResult]] | None = None) -> None:
        self._batches = list(batches or [])
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        results = self._batches.pop(0) if self._batches else []
        session = FakeSession(results)
        self.sessions.append(session)
        return session


def _req(**overrides: object) -> WorkflowHITLRequest:
    base: dict[str, object] = {
        "tenant_id": "tenant-1",
        "run_id": "run-1",
        "workflow_id": "wf-1",
        "step_id": "gate",
        "step_name": "Approval gate",
        "assigned_to": "reviewer-1",
        "priority": "high",
    }
    base.update(overrides)
    return WorkflowHITLRequest(**base)  # type: ignore[arg-type]


# ── save ─────────────────────────────────────────────────────────────────────


async def test_save_sets_tenant_inserts_and_commits() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult()]])
    store = PostgresWorkflowApprovalStore(db)
    req = _req()
    await store.save(req)

    session = db.sessions[0]
    assert session.committed
    set_tenant_sql, set_tenant_params = session.executed[0]
    assert "set_config" in set_tenant_sql
    assert set_tenant_params == {"tid": "tenant-1"}
    insert_sql, insert_params = session.executed[1]
    assert "INSERT INTO workflow_approvals" in insert_sql
    assert insert_params["request_id"] == req.request_id
    assert insert_params["status"] == "pending"
    assert insert_params["priority"] == "high"
    assert json.loads(insert_params["payload"])["request_id"] == req.request_id


async def test_save_null_workflow_id_becomes_none() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult()]])
    store = PostgresWorkflowApprovalStore(db)
    await store.save(_req(workflow_id=""))
    _, params = db.sessions[0].executed[1]
    assert params["workflow_id"] is None


# ── get ──────────────────────────────────────────────────────────────────────


async def test_get_without_tenant_returns_none_and_issues_no_query() -> None:
    db = FakeDBFactory()
    store = PostgresWorkflowApprovalStore(db)
    assert await store.get("some-id") is None
    assert db.sessions == []  # no session opened at all


async def test_get_found_parses_payload() -> None:
    req = _req()
    payload = json.dumps(dataclasses.asdict(req))
    db = FakeDBFactory([[FakeResult(), FakeResult(first=(payload,))]])
    store = PostgresWorkflowApprovalStore(db)
    got = await store.get(req.request_id, "tenant-1")
    assert got is not None
    assert got.request_id == req.request_id
    assert got.tenant_id == "tenant-1"


async def test_get_found_with_dict_payload_not_string() -> None:
    req = _req()
    payload_dict = dataclasses.asdict(req)
    db = FakeDBFactory([[FakeResult(), FakeResult(first=(payload_dict,))]])
    store = PostgresWorkflowApprovalStore(db)
    got = await store.get(req.request_id, "tenant-1")
    assert got is not None
    assert got.request_id == req.request_id


async def test_get_missing_returns_none() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult(first=None)]])
    store = PostgresWorkflowApprovalStore(db)
    assert await store.get("missing", "tenant-1") is None


async def test_get_ignores_stray_columns_in_payload() -> None:
    req = _req()
    payload_dict = {**dataclasses.asdict(req), "stray_extra_field": "should be dropped"}
    db = FakeDBFactory([[FakeResult(), FakeResult(first=(payload_dict,))]])
    store = PostgresWorkflowApprovalStore(db)
    got = await store.get(req.request_id, "tenant-1")
    assert got is not None
    assert not hasattr(got, "stray_extra_field")


# ── list_pending ─────────────────────────────────────────────────────────────


async def test_list_pending_no_filters_sorts_by_priority_then_created_at() -> None:
    now = datetime.now(UTC)
    low = _req(request_id="r-low", priority="low", created_at=now.isoformat())
    critical = _req(
        request_id="r-critical", priority="critical", created_at=(now + timedelta(seconds=1)).isoformat()
    )
    rows = [
        (json.dumps(dataclasses.asdict(low)),),
        (json.dumps(dataclasses.asdict(critical)),),
    ]
    db = FakeDBFactory([[FakeResult(), FakeResult(all_=rows)]])
    store = PostgresWorkflowApprovalStore(db)
    items, total = await store.list_pending("tenant-1")
    assert total == 2
    assert [item.request_id for item in items] == ["r-critical", "r-low"]


async def test_list_pending_filters_by_assigned_to_and_priority() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult(all_=[])]])
    store = PostgresWorkflowApprovalStore(db)
    items, total = await store.list_pending("tenant-1", assigned_to="alice", priority="high")
    assert items == []
    assert total == 0
    query_sql, params = db.sessions[0].executed[1]
    assert "assigned_to = :assigned_to" in query_sql
    assert "priority = :priority" in query_sql
    assert params == {"assigned_to": "alice", "priority": "high"}


async def test_list_pending_paginates() -> None:
    rows = [(json.dumps(dataclasses.asdict(_req(request_id=f"r-{i}"))),) for i in range(5)]
    db = FakeDBFactory([[FakeResult(), FakeResult(all_=rows)]])
    store = PostgresWorkflowApprovalStore(db)
    items, total = await store.list_pending("tenant-1", page=2, per_page=2)
    assert total == 5
    assert len(items) == 2


# ── get_stats ────────────────────────────────────────────────────────────────


async def test_get_stats_counts_pending_and_computes_avg_resolution() -> None:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    reviewed = created + timedelta(seconds=30)
    decided = _req(
        request_id="r-decided",
        created_at=created.isoformat(),
        reviewed_at=reviewed.isoformat(),
        status="decided",
    )
    pending = _req(request_id="r-pending", status="pending")
    rows = [
        ("decided", json.dumps(dataclasses.asdict(decided))),
        ("pending", json.dumps(dataclasses.asdict(pending))),
    ]
    db = FakeDBFactory([[FakeResult(), FakeResult(all_=rows)]])
    store = PostgresWorkflowApprovalStore(db)
    stats = await store.get_stats("tenant-1")
    assert stats["pending_count"] == 1
    assert stats["total_requests"] == 2
    assert stats["avg_resolution_seconds"] == 30.0


async def test_get_stats_no_reviewed_requests_yields_zero_avg() -> None:
    rows = [("pending", json.dumps(dataclasses.asdict(_req(request_id="r-1"))))]
    db = FakeDBFactory([[FakeResult(), FakeResult(all_=rows)]])
    store = PostgresWorkflowApprovalStore(db)
    stats = await store.get_stats("tenant-1")
    assert stats["avg_resolution_seconds"] == 0.0


async def test_get_stats_tolerates_malformed_timestamps() -> None:
    bad = _req(request_id="r-bad", reviewed_at="not-a-timestamp", status="decided")
    rows = [("decided", json.dumps(dataclasses.asdict(bad)))]
    db = FakeDBFactory([[FakeResult(), FakeResult(all_=rows)]])
    store = PostgresWorkflowApprovalStore(db)
    stats = await store.get_stats("tenant-1")
    assert stats["avg_resolution_seconds"] == 0.0
    assert stats["total_requests"] == 1


# ── list_by_run ──────────────────────────────────────────────────────────────


async def test_list_by_run_maps_all_matching_rows() -> None:
    a = _req(request_id="r-a", run_id="run-x")
    b = _req(request_id="r-b", run_id="run-x")
    rows = [
        (json.dumps(dataclasses.asdict(a)),),
        (json.dumps(dataclasses.asdict(b)),),
    ]
    db = FakeDBFactory([[FakeResult(), FakeResult(all_=rows)]])
    store = PostgresWorkflowApprovalStore(db)
    results = await store.list_by_run("tenant-1", "run-x")
    assert {r.request_id for r in results} == {"r-a", "r-b"}


async def test_list_by_run_empty() -> None:
    db = FakeDBFactory([[FakeResult(), FakeResult(all_=[])]])
    store = PostgresWorkflowApprovalStore(db)
    assert await store.list_by_run("tenant-1", "missing-run") == []
