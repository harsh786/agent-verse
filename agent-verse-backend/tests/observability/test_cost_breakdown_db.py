"""Per-goal cost breakdowns are durable, tenant-scoped Postgres rows.

Regression: the breakdown lived in the process-local ``_goal_breakdowns`` dict
(optionally mirrored into Redis by the API lifespan only). A goal executed by a
Celery worker — or by another API replica — recorded its planner/executor/verifier
costs into THAT process's memory, so ``GET /goals/{id}/cost-metrics`` on any other
process returned an empty breakdown, and a restart lost it. The Redis mirror was
also a read-modify-write of one JSON blob, so concurrent role calls lost updates.

Now ``arecord_role_cost`` does an atomic additive UPSERT into
``goal_cost_breakdowns`` and ``aget_breakdown`` reads it back, both under
``sqlalchemy_rls_context`` for the owning tenant.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from app.observability import cost_breakdown as cb
from tests._rls_recorder import RlsRecordingDb, assert_tenant_scoped

_TENANT = uuid.uuid4().hex


class _Table:
    """Minimal goal_cost_breakdowns emulation driven by the recorded UPSERTs."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, str], list[Any]] = {}

    def rows_for(self, sql: str, params: dict[str, Any]) -> list[Any]:
        if sql.startswith("INSERT INTO goal_cost_breakdowns"):
            key = (params["tid"], params["gid"], params["role"], params["model"])
            row = self.rows.setdefault(key, [params["role"], params["model"], 0, 0, 0.0, 0])
            row[2] += params["in_tok"]
            row[3] += params["out_tok"]
            row[4] += params["cost"]
            row[5] += 1
            return []
        if sql.startswith("SELECT") and "goal_cost_breakdowns" in sql:
            return [
                tuple(r)
                for (tid, gid, _r, _m), r in self.rows.items()
                if tid == params["tid"] and gid == params["gid"]
            ]
        return []


@pytest.fixture
def db() -> Iterator[tuple[RlsRecordingDb, _Table]]:
    table = _Table()
    rec = RlsRecordingDb(rows_for=table.rows_for)
    cb.configure_db(rec)
    cb._goal_breakdowns.clear()
    yield rec, table
    cb.reset_db()
    cb._goal_breakdowns.clear()


async def test_recorded_costs_are_persisted_and_readable_from_a_fresh_process(
    db: tuple[RlsRecordingDb, _Table],
) -> None:
    rec, _table = db
    goal_id = f"goal-{uuid.uuid4().hex[:8]}"

    await cb.arecord_role_cost(goal_id, "planner", "nv", 100, 10, 0.5, tenant_id=_TENANT)
    await cb.arecord_role_cost(goal_id, "executor", "qwen", 200, 20, 0.0, tenant_id=_TENANT)
    await cb.arecord_role_cost(goal_id, "executor", "qwen", 50, 5, 0.25, tenant_id=_TENANT)

    # Nothing authoritative in process memory: another replica/worker has an empty dict.
    assert goal_id not in cb._goal_breakdowns
    cb._goal_breakdowns.clear()

    bd = await cb.aget_breakdown(goal_id, tenant_id=_TENANT)
    roles = {(e.role, e.model): e for e in bd.entries}
    assert roles[("planner", "nv")].input_tokens == 100
    assert roles[("executor", "qwen")].input_tokens == 250
    assert roles[("executor", "qwen")].calls == 2
    assert bd.total_cost() == pytest.approx(0.75)

    stmts = assert_tenant_scoped(rec, "goal_cost_breakdowns", _TENANT, min_statements=4)
    # Additive UPSERT — no read-modify-write of a shared blob.
    assert all("ON CONFLICT" in s.sql for s in stmts if s.sql.startswith("INSERT"))


async def test_other_tenant_reads_nothing(db: tuple[RlsRecordingDb, _Table]) -> None:
    goal_id = f"goal-{uuid.uuid4().hex[:8]}"
    await cb.arecord_role_cost(goal_id, "planner", "nv", 1, 1, 0.1, tenant_id=_TENANT)

    other = await cb.aget_breakdown(goal_id, tenant_id=uuid.uuid4().hex)
    assert other.entries == []


async def test_no_db_configured_uses_in_memory_fallback() -> None:
    cb.reset_db()
    goal_id = f"goal-mem-{uuid.uuid4().hex[:8]}"
    await cb.arecord_role_cost(goal_id, "verifier", "m", 3, 4, 0.0, tenant_id=_TENANT)
    bd = await cb.aget_breakdown(goal_id, tenant_id=_TENANT)
    assert [(e.role, e.input_tokens) for e in bd.entries] == [("verifier", 3)]
    cb._goal_breakdowns.pop(goal_id, None)


async def test_cost_metrics_endpoint_reads_the_durable_breakdown(
    db: tuple[RlsRecordingDb, _Table],
) -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.observability.cost_breakdown_api import get_goal_cost_metrics

    goal_id = f"goal-{uuid.uuid4().hex[:8]}"
    await cb.arecord_role_cost(goal_id, "planner", "nv", 7, 1, 0.0, tenant_id=_TENANT)
    cb._goal_breakdowns.clear()  # "another replica"

    class _Goals:
        async def get_goal(self, *, goal_id: str, tenant_ctx: Any) -> dict[str, str]:
            return {"goal_id": goal_id}

    request = MagicMock()
    request.state = SimpleNamespace(tenant=SimpleNamespace(tenant_id=_TENANT))
    request.app.state = SimpleNamespace(llm_response_cache=None, goal_service=_Goals())

    body = await get_goal_cost_metrics(goal_id, request)
    assert [(r["role"], r["input_tokens"]) for r in body["roles"]] == [("planner", 7)]


async def test_failover_provenance_is_sent_with_the_upsert(
    db: tuple[RlsRecordingDb, _Table],
) -> None:
    """``fallback_from`` (the models a call failed over from) reaches the row."""
    import json

    rec, _table = db
    goal_id = f"goal-{uuid.uuid4().hex[:8]}"
    await cb.arecord_role_cost(goal_id, "executor", "qwen", 1, 1, 0.0, tenant_id=_TENANT,
                               fallback_from=["dead", "dead", ""])
    await cb.arecord_role_cost(goal_id, "executor", "qwen", 1, 1, 0.0, tenant_id=_TENANT)

    upserts = [s for s in rec.touching("goal_cost_breakdowns") if s.sql.startswith("INSERT")]
    assert [json.loads(s.params["fallback"]) for s in upserts] == [["dead"], []]
    assert "fallback_from" in upserts[0].sql
