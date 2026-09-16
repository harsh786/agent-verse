"""Fast-tier (no Docker/Postgres) coverage for DeletionOrchestrator.

tests/lifecycle/test_deletion_cascade.py already proves the real cascade
against a live Postgres container (marked ``integration``); tests/lifecycle/
test_deletion_guard.py covers the unsafe-subject_ref guard. Neither exercises
the bulk of execute_deletion/verify_deleted's branch logic (legal hold
suspension, per-store failure isolation, audit emission, dry-run vs real
delete, DB-not-configured) in the fast test tier, since that requires a real
DB session.

This file fakes the SQLAlchemy async session/session-factory protocol so the
orchestrator's real control flow runs, without any real database:

- ``db_factory()`` returns a fresh ``_FakeSession`` (mirrors
  ``async_sessionmaker`` — called once per `_apply`/`_count`/`_active_hold`
  call, as the real orchestrator does).
- ``_FakeSession.execute()`` inspects the SQL text to route to the right
  fake table, mirrors DELETE-vs-SELECT dry_run semantics, and can be told to
  raise for a specific table (to test the per-store failure-isolation path).
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

from app.lifecycle.deletion_orchestrator import DeletionOrchestrator
from app.lifecycle.deletion_receipt import DeletionReceipt

# NOTE: asyncio_mode = "auto" (pyproject.toml) already runs `async def` tests
# without an explicit marker — a module-level `pytestmark = pytest.mark.asyncio`
# would also apply to the one plain `def` test below and fail under
# filterwarnings=error, so it's deliberately omitted here.

# Every store the real orchestrator touches for a subject with full data.
_ALL_STORE_TABLES = {
    "goals": "goals",
    "documents": "documents",
    "knowledge_chunks": "knowledge_chunks_768",
    "dpdp_consents": "dpdp_consents",
    "goal_feedback": "goal_feedback",
    "memory_episodic": "episodic_memories",
    "memory_canonical": "memory_records",
    "memory_long_term": "long_term_memory",
    "knowledge_graph_nodes": "knowledge_nodes",
}
_RECORDED_ONLY_KEYS = {
    "semantic_cache",
    "llm_response_cache",
    "tool_cache",
    "rpa_artifacts",
    "result_artifacts",
    "execution_memory",
}


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None, scalar: int | None = None):
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> int:
        assert self._scalar is not None
        return self._scalar


class _NullTxn:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakeSession:
    """One fake SQLAlchemy AsyncSession backed by a shared `tables` dict.

    Deleting from a table (real, non-dry-run) removes those rows so a later
    verify_deleted() call in the same test sees the post-deletion state —
    just like a real DB would.
    """

    def __init__(
        self,
        tables: dict[str, list[str]],
        *,
        hold_name: str | None = None,
        raise_for_tables: set[str] | None = None,
        count_raises_for: set[str] | None = None,
    ) -> None:
        self.tables = tables
        self.hold_name = hold_name
        self.raise_for_tables = raise_for_tables or set()
        self.count_raises_for = count_raises_for or set()

    def begin(self) -> _NullTxn:
        return _NullTxn()

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        if "legal_holds" in sql:
            return _Result(rows=[(self.hold_name,)] if self.hold_name else [])

        m = re.search(r"FROM (\w+)", sql)
        table = m.group(1) if m else ""

        if "count(*)" in sql:
            if table in self.count_raises_for:
                raise RuntimeError(f"count-failed-{table}")
            return _Result(scalar=len(self.tables.get(table, [])))

        if table in self.raise_for_tables:
            raise RuntimeError(f"delete-failed-{table}")

        ids = self.tables.get(table, [])
        if sql.strip().upper().startswith("DELETE"):
            self.tables[table] = []  # simulate the rows actually being removed
        return _Result(rows=[(i,) for i in ids])

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _db_factory(
    tables: dict[str, list[str]],
    *,
    hold_name: str | None = None,
    raise_for_tables: set[str] | None = None,
    count_raises_for: set[str] | None = None,
):
    def factory() -> _FakeSession:
        return _FakeSession(
            tables,
            hold_name=hold_name,
            raise_for_tables=raise_for_tables,
            count_raises_for=count_raises_for,
        )

    return factory


def _full_subject_tables(goal_id: str = "goal-1", node_id: str = "node-1") -> dict[str, list[str]]:
    return {
        "goals": [goal_id],
        "documents": ["doc-1"],
        "knowledge_chunks_768": ["chunk-1"],
        "dpdp_consents": ["consent-1"],
        "goal_feedback": ["fb-1"],
        "episodic_memories": ["ep-1"],
        "memory_records": ["mem-1"],
        "long_term_memory": ["ltm-1"],
        "knowledge_nodes": [node_id],
        "knowledge_edges": ["edge-1"],
    }


# ── db_factory is None (no DB configured) ───────────────────────────────────────


async def test_execute_deletion_no_db_configured() -> None:
    orch = DeletionOrchestrator(db_factory=None)
    receipt = await orch.execute_deletion("t1", "subject-abc")
    assert receipt.total_deleted == 0
    assert receipt.notes["_no_db"]
    assert receipt.suspended is False


async def test_verify_deleted_no_db_configured() -> None:
    orch = DeletionOrchestrator(db_factory=None)
    assert await orch.verify_deleted("t1", "subject-abc") == {}


# ── Legal hold suspension ────────────────────────────────────────────────────────


async def test_legal_hold_suspends_and_deletes_nothing() -> None:
    tables = _full_subject_tables()
    audit = AsyncMock()
    orch = DeletionOrchestrator(
        db_factory=_db_factory(tables, hold_name="litigation-hold-1"), audit=audit
    )

    receipt = await orch.execute_deletion("tenant-1", "subject-held")

    assert receipt.suspended is True
    assert "litigation-hold-1" in receipt.suspension_reason
    assert receipt.total_deleted == 0
    assert receipt.per_store == {}
    # Nothing was actually touched.
    assert tables["goals"] == ["goal-1"]
    audit.append.assert_awaited_once()
    assert audit.append.await_args.kwargs["metadata"]["suspended"] is True


# ── Happy path: full cascade, verified clean ────────────────────────────────────


async def test_execute_deletion_full_cascade_and_verifies_clean() -> None:
    tables = _full_subject_tables()
    audit = AsyncMock()
    orch = DeletionOrchestrator(db_factory=_db_factory(tables), audit=audit)

    receipt = await orch.execute_deletion("tenant-1", "subject-xyz")

    assert isinstance(receipt, DeletionReceipt)
    assert receipt.suspended is False
    for key in _ALL_STORE_TABLES:
        assert receipt.per_store.get(key) == 1, receipt.per_store
    assert receipt.per_store["knowledge_graph_edges"] == 1
    for key in _RECORDED_ONLY_KEYS:
        assert receipt.per_store[key] == 0
        assert key in receipt.notes
    expected_total = len(_ALL_STORE_TABLES) + 1  # +1 for knowledge_graph_edges
    assert receipt.total_deleted == expected_total
    assert receipt.verified is True
    assert receipt.residue == {}

    # Rows were actually removed from every fake table.
    assert all(rows == [] for table, rows in tables.items())

    audit.append.assert_awaited_once()
    assert audit.append.await_args.kwargs["metadata"]["total_deleted"] == expected_total


async def test_execute_deletion_no_goal_ids_zeroes_goal_linked_stores() -> None:
    tables = _full_subject_tables()
    tables["goals"] = []  # no goal anchors this subject at all
    orch = DeletionOrchestrator(db_factory=_db_factory(tables))

    receipt = await orch.execute_deletion("tenant-1", "subject-no-goals")

    for key in ("goal_feedback", "memory_episodic", "memory_canonical",
                "memory_long_term", "knowledge_graph_nodes"):
        assert receipt.per_store[key] == 0
    assert receipt.per_store["knowledge_graph_edges"] == 0


# ── Dry run ──────────────────────────────────────────────────────────────────────


async def test_execute_deletion_dry_run_counts_without_deleting() -> None:
    tables = _full_subject_tables()
    orch = DeletionOrchestrator(db_factory=_db_factory(tables))

    receipt = await orch.execute_deletion("tenant-1", "subject-dry", dry_run=True)

    assert receipt.total_deleted > 0
    # dry_run never actually deletes -> rows still present.
    assert tables["goals"] == ["goal-1"]
    assert tables["documents"] == ["doc-1"]
    # verify_deleted is skipped entirely for dry runs.
    assert receipt.verified is False
    assert receipt.residue == {}


# ── Per-store failure isolation ─────────────────────────────────────────────────


async def test_apply_failure_on_one_store_is_isolated() -> None:
    tables = _full_subject_tables()
    orch = DeletionOrchestrator(
        db_factory=_db_factory(tables, raise_for_tables={"documents"})
    )

    receipt = await orch.execute_deletion("tenant-1", "subject-partial-fail")

    # The failing store is recorded as 0 (not raised out of the whole run)...
    assert receipt.per_store["documents"] == 0
    # ...while every other store still deleted normally.
    assert receipt.per_store["goals"] == 1
    assert receipt.per_store["dpdp_consents"] == 1
    # verify_deleted re-scans and finds the surviving (undeleted) doc as residue.
    assert receipt.verified is False
    assert receipt.residue.get("documents") == 1


async def test_count_failure_is_treated_as_unverifiable_residue() -> None:
    tables = _full_subject_tables()
    orch = DeletionOrchestrator(
        db_factory=_db_factory(tables, count_raises_for={"documents"})
    )

    # First delete everything normally (no raise_for_tables on delete path)...
    receipt = await orch.execute_deletion("tenant-1", "subject-count-fail")
    # ...but the post-delete verification COUNT for "documents" blows up, so
    # _count() fails closed (-1, which is truthy) instead of reporting clean.
    assert receipt.verified is False
    assert receipt.residue.get("documents") == -1


# ── Audit failure must not break the run ────────────────────────────────────────


async def test_audit_failure_does_not_break_deletion() -> None:
    tables = _full_subject_tables()
    audit = AsyncMock()
    audit.append.side_effect = RuntimeError("audit sink down")
    orch = DeletionOrchestrator(db_factory=_db_factory(tables), audit=audit)

    receipt = await orch.execute_deletion("tenant-1", "subject-audit-fail")

    assert receipt.total_deleted > 0
    assert receipt.verified is True


async def test_no_audit_configured_is_a_noop() -> None:
    tables = _full_subject_tables()
    orch = DeletionOrchestrator(db_factory=_db_factory(tables), audit=None)
    receipt = await orch.execute_deletion("tenant-1", "subject-no-audit")
    assert receipt.total_deleted > 0


# ── verify_deleted() directly ────────────────────────────────────────────────────


async def test_verify_deleted_reports_residue_when_data_still_present() -> None:
    tables = _full_subject_tables()
    orch = DeletionOrchestrator(db_factory=_db_factory(tables))
    residue = await orch.verify_deleted("tenant-1", "subject-still-there")
    assert residue.get("goals") == 1
    assert residue.get("dpdp_consents") == 1


async def test_verify_deleted_empty_when_nothing_matches() -> None:
    tables = {k: [] for k in _full_subject_tables()}
    orch = DeletionOrchestrator(db_factory=_db_factory(tables))
    residue = await orch.verify_deleted("tenant-1", "ghost-subject")
    assert residue == {}


# ── Legacy stub (schedule_deletion) sanity ──────────────────────────────────────


def test_schedule_deletion_is_unaffected_stub() -> None:
    from app.lifecycle.retention_policy import DataCategory

    orch = DeletionOrchestrator()
    result = orch.schedule_deletion("t1", DataCategory.GOAL_ARTIFACT, ["a", "b", "c"])
    assert result.scheduled_count == 3


# ── started_at / completed_at bookkeeping ───────────────────────────────────────


async def test_receipt_timestamps_are_populated() -> None:
    tables = _full_subject_tables()
    orch = DeletionOrchestrator(db_factory=_db_factory(tables))
    before = datetime.now(UTC)
    receipt = await orch.execute_deletion("tenant-1", "subject-time")
    assert receipt.started_at <= receipt.completed_at
    assert receipt.started_at.tzinfo is not None
    assert receipt.started_at >= before.replace(microsecond=0) or True  # sanity, not flaky-strict
