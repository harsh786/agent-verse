"""Regression: _write_checkpoint must upsert, not INSERT.

The agent loop re-checkpoints the same step on every replan. A plain INSERT
collided with uq_goal_checkpoints_key (tenant_id, goal_id, checkpoint_key),
raising a UniqueViolationError that was swallowed — so replanned state was never
re-persisted and crash-recovery would restore a stale checkpoint. Writing the
same (tenant, goal, checkpoint_key) twice must now update in place.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.intelligence.guardrails import GuardrailChecker
from app.providers.fake import FakeProvider
from app.reliability.dedup import DeduplicationCache
from app.reliability.result_processor import ResultProcessor
from app.reliability.rollback import RollbackEngine
from app.tenancy.context import PlanTier, TenantContext

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse",
)


def _build_graph() -> AgentGraph:
    fake = FakeProvider(responses=["done"])
    return AgentGraph(
        planner=fake,
        executor=fake,
        verifier=fake,
        result_processor=ResultProcessor(),
        dedup_cache=DeduplicationCache(),
        rollback_engine=RollbackEngine(),
        guardrail_checker=GuardrailChecker(),
    )


@pytest.mark.integration
async def test_write_checkpoint_upserts_same_step_on_replan() -> None:
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid.uuid4().hex
    goal_id = uuid.uuid4().hex
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")

    graph = _build_graph()
    graph._db_session_factory = factory  # normally wired by main.py after construction

    try:
        # Seed the FK parents (tenants, goals) under the tenant's RLS context.
        async with factory() as db, db.begin():
            await db.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id}
            )
            await db.execute(
                text("INSERT INTO tenants (id, name, email) VALUES (:id, :n, :e)"),
                {"id": tenant_id, "n": "ckpt-test", "e": f"{tenant_id}@test.local"},
            )
            await db.execute(
                text("INSERT INTO goals (id, tenant_id, goal_text) VALUES (:id, :t, :g)"),
                {"id": goal_id, "t": tenant_id, "g": "checkpoint upsert goal"},
            )

        # First checkpoint of step 0.
        state1 = AgentState(goal="test", tenant_ctx=ctx, goal_id=goal_id)
        state1.iterations = 1
        state1.plan = ["step one"]
        await graph._write_checkpoint(goal_id, 0, state1, ctx)

        # Replan → re-checkpoint the SAME step 0 with new state. Must not raise.
        state2 = AgentState(goal="test", tenant_ctx=ctx, goal_id=goal_id)
        state2.iterations = 4
        state2.plan = ["step one v2", "step two"]
        await graph._write_checkpoint(goal_id, 0, state2, ctx)

        # Exactly one row for step_0, carrying the SECOND (replanned) payload.
        async with factory() as db, db.begin():
            await db.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id}
            )
            rows = (
                await db.execute(
                    text(
                        "SELECT payload, recovery_status FROM goal_checkpoints "
                        "WHERE tenant_id=:t AND goal_id=:g AND checkpoint_key='step_0'"
                    ),
                    {"t": tenant_id, "g": goal_id},
                )
            ).fetchall()

        assert len(rows) == 1, f"expected a single upserted row, got {len(rows)}"
        payload = rows[0][0]
        assert payload["iterations"] == 4, "checkpoint payload was not updated on replan"
        assert payload["plan"] == ["step one v2", "step two"]
        assert rows[0][1] == "checkpointed"
    finally:
        async with factory() as db, db.begin():
            await db.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id}
            )
            # goal_checkpoints cascades from goals/tenants, but clean up explicitly.
            await db.execute(
                text("DELETE FROM goal_checkpoints WHERE tenant_id=:t"), {"t": tenant_id}
            )
            await db.execute(text("DELETE FROM goals WHERE tenant_id=:t"), {"t": tenant_id})
            await db.execute(text("DELETE FROM tenants WHERE id=:t"), {"t": tenant_id})
        await engine.dispose()
