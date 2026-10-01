"""CORE-10 on real Postgres: a goal-tree child's completion survives a worker restart."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.fanout_ledger import ledger_for
from app.agent.goal_tree import execute_goal_tree
from tests.agent.test_goal_tree_resume import _ChildGraph, _Planner


@pytest.mark.integration
async def test_child_completion_survives_a_worker_restart(pg_url: str) -> None:
    from app.tenancy.context import PlanTier, TenantContext

    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id, parent = uuid.uuid4().hex, uuid.uuid4().hex
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")

    async def _exec(sql: str, params: dict[str, Any]) -> None:
        async with factory() as s, s.begin():
            await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
            await s.execute(text(sql), params)

    async def _tree(ran: list[str], planner: _Planner, crash_on: str | None) -> Any:
        # A fresh ledger per run, as a restarted worker would build.
        return await execute_goal_tree(
            "close the books", planner=planner, tenant_ctx=ctx, parent_goal_id=parent,
            graph_factory=lambda: _ChildGraph(ran, crash_on),
            ledger=ledger_for(factory, tenant_id=tenant_id, parent_goal_id=parent,
                              kind="goal_tree"),
        )

    try:
        await _exec("INSERT INTO tenants (id, name, email) VALUES (:t, 'tree', :e)",
                    {"t": tenant_id, "e": f"{tenant_id}@t.local"})
        await _exec("INSERT INTO goals (id, tenant_id, goal_text, status) "
                    "VALUES (:i, :t, 'close the books', 'executing')",
                    {"i": parent, "t": tenant_id})
        planner = _Planner()
        ran: list[str] = []
        with pytest.raises(asyncio.CancelledError):
            await _tree(ran, planner, crash_on="file the receipt")
        assert ran == ["send the invoice"]

        ran_after: list[str] = []
        await _tree(ran_after, planner, crash_on=None)
        assert ran_after == ["file the receipt"]
        assert planner.decompositions == 1
    finally:
        await _exec("DELETE FROM goals WHERE tenant_id = :t", {"t": tenant_id})
        await _exec("DELETE FROM tenants WHERE id = :t", {"t": tenant_id})
        await engine.dispose()
