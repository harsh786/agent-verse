"""Simulation runs are durable, tenant-scoped Postgres rows.

Regression: ``SimulationRunner`` kept runs in ``self._runs`` / ``self._run_tenant``
dicts (the tenant map was added for isolation, but storage stayed process-local).
``POST /enterprise/simulation`` on one API replica followed by
``GET /enterprise/simulation/{run_id}`` on another returned 404, and every restart
erased all runs. With a DB bound (lifespan), runs are written to
``simulation_runs`` and read back under ``sqlalchemy_rls_context``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.enterprise.simulation import SimulationRunner
from app.tenancy.context import PlanTier, TenantContext
from tests._rls_recorder import RlsRecordingDb, assert_tenant_scoped

_TID = uuid.uuid4().hex
_CTX = TenantContext(tenant_id=_TID, plan=PlanTier.ENTERPRISE, api_key_id="k")


class _Table:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], str] = {}

    def rows_for(self, sql: str, params: dict[str, Any]) -> list[Any]:
        if sql.startswith("INSERT INTO simulation_runs"):
            self.rows[(params["tid"], params["rid"])] = params["payload"]
            return []
        if sql.startswith("SELECT") and "simulation_runs" in sql:
            if "rid" in params:
                p = self.rows.get((params["tid"], params["rid"]))
                return [(p,)] if p is not None else []
            return [(p,) for (tid, _r), p in self.rows.items() if tid == params["tid"]]
        return []


async def test_run_started_on_one_replica_is_readable_on_another() -> None:
    table = _Table()
    db = RlsRecordingDb(rows_for=table.rows_for)

    replica_a = SimulationRunner()
    replica_a.set_db(db)
    run = await replica_a.start(goal="notify the team", mock_tools={}, tenant_ctx=_CTX)

    # Not held in replica A's memory either — the DB row is authoritative.
    assert run.run_id not in replica_a._runs

    replica_b = SimulationRunner()  # fresh process memory
    replica_b.set_db(db)
    fetched = await replica_b.aget(run_id=run.run_id, tenant_ctx=_CTX)
    assert fetched is not None
    assert fetched.run_id == run.run_id
    assert fetched.goal == "notify the team"
    assert fetched.status == run.status
    assert fetched.result == json.loads(json.dumps(run.result))

    listed = await replica_b.alist_runs(tenant_ctx=_CTX)
    assert [r.run_id for r in listed] == [run.run_id]

    assert_tenant_scoped(db, "simulation_runs", _TID, min_statements=3)


async def test_other_tenant_cannot_read_the_run() -> None:
    table = _Table()
    db = RlsRecordingDb(rows_for=table.rows_for)
    runner = SimulationRunner()
    runner.set_db(db)
    run = await runner.start(goal="g", mock_tools={}, tenant_ctx=_CTX)

    other = TenantContext(tenant_id=uuid.uuid4().hex, plan=PlanTier.FREE, api_key_id="o")
    assert await runner.aget(run_id=run.run_id, tenant_ctx=other) is None
    assert await runner.alist_runs(tenant_ctx=other) == []


async def test_no_db_keeps_the_in_memory_fallback() -> None:
    runner = SimulationRunner()
    run = await runner.start(goal="g", mock_tools={}, tenant_ctx=_CTX)
    assert (await runner.aget(run_id=run.run_id, tenant_ctx=_CTX)) is run
    assert runner.get(run_id=run.run_id, tenant_ctx=_CTX) is run
    other = TenantContext(tenant_id=uuid.uuid4().hex, plan=PlanTier.FREE, api_key_id="o")
    assert await runner.aget(run_id=run.run_id, tenant_ctx=other) is None


async def test_get_simulation_endpoint_reads_through_the_db() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.api.enterprise import get_simulation

    table = _Table()
    db = RlsRecordingDb(rows_for=table.rows_for)
    writer = SimulationRunner()
    writer.set_db(db)
    run = await writer.start(goal="g", mock_tools={}, tenant_ctx=_CTX)

    reader = SimulationRunner()
    reader.set_db(db)
    request = MagicMock()
    request.state = SimpleNamespace(tenant=_CTX)
    request.app.state = SimpleNamespace(simulation_runner=reader)

    body = await get_simulation(request, run.run_id)
    assert body["run_id"] == run.run_id
