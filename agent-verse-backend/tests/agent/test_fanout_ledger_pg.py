"""CORE-31 / CORE-10 on real Postgres: the fan-out ledger survives a crash.

A supervisor parent is run against a goal service that inserts real ``goals``
rows for its sub-goals; the worker "dies" after the first sub-goal row exists
but before the ledger learned its id. The redelivered parent re-attaches to
the existing rows: no duplicate sub-goal row is ever created.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.fanout_ledger import FANOUT_TASK_KEY, LedgerEntry, ledger_for
from app.agent.supervisor import SUBGOAL_MARKER, SupervisorAgent
from app.providers.base import CompletionResponse
from app.tenancy.context import PlanTier, TenantContext


class _Planner:
    async def complete(self, request: Any) -> CompletionResponse:
        if "decomposer" in request.messages[0].content:
            content = '{"sub_tasks": [{"goal": "research A"}, {"goal": "research B"}]}'
        else:
            content = "synthesized"
        return CompletionResponse(content=content, model="m")


class _RowGoalService:
    """submit_goal inserts a real goals row, like GoalService does."""

    def __init__(self, factory: Any, tenant_id: str, *, crash_after: int | None) -> None:
        self.factory, self.tenant_id, self.crash_after = factory, tenant_id, crash_after
        self.submitted = 0

    async def submit_goal(self, **kwargs: Any) -> dict[str, Any]:
        gid = uuid.uuid4().hex
        ctx = kwargs["execution_context"]
        async with self.factory() as s, s.begin():
            await s.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": self.tenant_id}
            )
            await s.execute(
                text(
                    "INSERT INTO goals (id, tenant_id, parent_goal_id, goal_text, status, "
                    "execution_context) VALUES (:i, :t, :p, :g, 'complete', CAST(:c AS JSON))"
                ),
                {"i": gid, "t": self.tenant_id, "p": ctx[SUBGOAL_MARKER], "g": kwargs["goal"],
                 "c": json.dumps(ctx)},
            )
        self.submitted += 1
        if self.crash_after is not None and self.submitted >= self.crash_after:
            raise asyncio.CancelledError  # the worker dies right after the INSERT
        return {"goal_id": gid}

    async def subscribe_events(self, goal_id: str, tenant_ctx: Any) -> AsyncIterator[dict]:
        yield {"type": "goal_complete", "answer": f"answer {goal_id}"}


@pytest.mark.integration
async def test_redelivered_supervisor_creates_no_duplicate_sub_goal_rows(pg_url: str) -> None:
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id, parent = uuid.uuid4().hex, uuid.uuid4().hex
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")

    async def _exec(sql: str, params: dict[str, Any]) -> Any:
        async with factory() as s, s.begin():
            await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
            res = await s.execute(text(sql), params)
            return res.all() if sql.startswith("SELECT") else None

    try:
        await _exec("INSERT INTO tenants (id, name, email) VALUES (:t, 'fan', :e)",
                    {"t": tenant_id, "e": f"{tenant_id}@t.local"})
        await _exec("INSERT INTO goals (id, tenant_id, goal_text, status) "
                    "VALUES (:i, :t, 'research', 'executing')", {"i": parent, "t": tenant_id})
        assert await _exec("SELECT id FROM goals WHERE id = :i", {"i": parent}), "parent row"

        def _ledger() -> Any:
            return ledger_for(factory, tenant_id=tenant_id, parent_goal_id=parent,
                              kind="supervisor")

        crashed = _RowGoalService(factory, tenant_id, crash_after=1)
        with pytest.raises(asyncio.CancelledError):
            await SupervisorAgent(planner_provider=_Planner(), goal_service=crashed).run(
                goal="research", tenant_ctx=ctx, parent_goal_id=parent, ledger=_ledger()
            )

        redelivered = _RowGoalService(factory, tenant_id, crash_after=None)
        result = await SupervisorAgent(planner_provider=_Planner(), goal_service=redelivered).run(
            goal="research", tenant_ctx=ctx, parent_goal_id=parent, ledger=_ledger()
        )
        assert result.success
        # Every planned child is submitted exactly once across both runs.
        assert crashed.submitted + redelivered.submitted == 2

        rows = await _exec(
            "SELECT execution_context ->> :m FROM goals WHERE tenant_id = :t "
            "AND parent_goal_id = :p", {"m": FANOUT_TASK_KEY, "t": tenant_id, "p": parent}
        )
        keys = [r[0] for r in rows]
        assert len(keys) == 2 and len(set(keys)) == 2  # one row per planned task

        entries = await _ledger().load()
        assert [e.status for e in entries] == ["complete", "complete"]
        assert all(e.child_goal_id for e in entries)

        # Goal-tree style use: completion survives a new ledger instance.
        tree = ledger_for(factory, tenant_id=tenant_id, parent_goal_id=parent, kind="goal_tree")
        assert tree is not None
        await tree.plan([LedgerEntry(task_key="sg-1", position=0, spec={"d": "x"})])
        await tree.mark_finished("sg-1", status="complete", result="done")
        again = ledger_for(factory, tenant_id=tenant_id, parent_goal_id=parent, kind="goal_tree")
        assert again is not None
        assert [(e.status, e.result) for e in await again.load()] == [("complete", "done")]

        # Tenant isolation: another tenant's context sees none of these rows.
        other = ledger_for(factory, tenant_id=uuid.uuid4().hex, parent_goal_id=parent,
                           kind="supervisor")
        assert other is not None and await other.load() == []
    finally:
        await _exec("DELETE FROM goals WHERE tenant_id = :t", {"t": tenant_id})
        await _exec("DELETE FROM tenants WHERE id = :t", {"t": tenant_id})
        await engine.dispose()
