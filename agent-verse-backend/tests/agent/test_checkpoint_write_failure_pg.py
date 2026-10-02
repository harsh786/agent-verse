"""CORE-26 on real Postgres: a dropped connection during a checkpoint write is retried.

The first attempt's connection "drops"; the retry lands the checkpoint row, so
the completed step stays durable and the goal is not flagged degraded. A
resume then sees the step as done (no repeat).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.checkpoint_resume import (
    COMPLETED_STEPS_KEY,
    EXECUTABLE_PLAN_KEY,
    restore_from_checkpoint,
)
from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext


@pytest.mark.integration
async def test_checkpoint_survives_a_dropped_connection(pg_url: str) -> None:
    engine = create_async_engine(pg_url)
    real = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id, goal_id = uuid.uuid4().hex, uuid.uuid4().hex
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")
    drops = {"left": 1}

    class _Dropped:
        async def __aenter__(self) -> None:
            raise ConnectionResetError("connection dropped mid-write")

        async def __aexit__(self, *exc: object) -> None:
            return None

    def _flaky() -> Any:
        if drops["left"]:
            drops["left"] -= 1
            return _Dropped()
        return real()

    async def _exec(sql: str, params: dict[str, Any]) -> list[Any]:
        async with real() as s, s.begin():
            await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
            res = await s.execute(text(sql), params)
            return list(res.all()) if res.returns_rows else []

    try:
        await _exec("INSERT INTO tenants (id, name, email) VALUES (:t, 'ck', :e)",
                    {"t": tenant_id, "e": f"{tenant_id}@t.local"})
        await _exec("INSERT INTO goals (id, tenant_id, goal_text, status) "
                    "VALUES (:i, :t, 'send invoice', 'executing')", {"i": goal_id, "t": tenant_id})
        p = FakeProvider()
        graph = AgentGraph(planner=p, executor=p, verifier=p)
        graph._db_session_factory = _flaky
        graph._CHECKPOINT_RETRY_DELAYS_S = (0.0, 0.0)  # type: ignore[misc]
        state = AgentState(goal="send invoice", tenant_ctx=ctx)
        state.goal_id = goal_id
        state.steps.append(StepResult(description="send the invoice email",
                                      status=StepStatus.COMPLETE, output="sent"))
        state.context[EXECUTABLE_PLAN_KEY] = ["send the invoice email", "file the receipt"]
        state.context[COMPLETED_STEPS_KEY] = {"s1": "sent"}

        await graph._write_checkpoint(goal_id, 0, state, ctx)

        assert "checkpoint_degraded" not in state.context
        rows = await _exec(
            "SELECT checkpoint_key FROM goal_checkpoints WHERE goal_id = :g AND tenant_id = :t",
            {"g": goal_id, "t": tenant_id},
        )
        assert [r[0] for r in rows] == ["step_0"]

        graph._db_session_factory = real
        payload = await graph._load_checkpoint(goal_id, ctx)
        resumed = restore_from_checkpoint(payload, None, goal="send invoice", tenant_ctx=ctx,
                                          goal_id=goal_id)
        assert resumed is not None  # the completed step is known: it will not repeat
        assert resumed.context.get(COMPLETED_STEPS_KEY) == {"s1": "sent"}
    finally:
        await _exec("DELETE FROM goal_checkpoints WHERE tenant_id = :t", {"t": tenant_id})
        await _exec("DELETE FROM goals WHERE tenant_id = :t", {"t": tenant_id})
        await _exec("DELETE FROM tenants WHERE id = :t", {"t": tenant_id})
        await engine.dispose()
