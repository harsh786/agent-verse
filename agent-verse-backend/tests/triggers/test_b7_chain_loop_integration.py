"""B7 against real Postgres + Redis: a trigger chain fires once and never loops.

Goal A completes -> its ``goal.completed`` event goes through the real trigger
stream to two replica consumers -> the real dispatcher (Redis dedup + rate
limit + bulkhead, Postgres ``trigger_events`` audit) creates goal B exactly once.
Goal B is stamped with its lineage in ``goals.execution_context``; when B
completes (published twice, as two relaying replicas would) the trigger does not
re-fire on it, and the refusal is audited.

The same for ``memory.created``: the event of a memory written by the goal a
memory_created trigger started carries only the goal id, and the consumer reads
that goal's lineage from the goals row (least-privilege role, RLS + tenant
predicate) — so the trigger does not loop on its own goal's learning.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/triggers/test_b7_chain_loop_integration.py -m integration
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.rls import sqlalchemy_rls_context
from app.tenancy.context import PlanTier, TenantContext
from app.triggers import bus
from app.triggers.consumers.chain import ChainTriggerConsumer, build_chain_event
from app.triggers.consumers.memory import MemoryTriggerConsumer
from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.lineage import lineage_from_context
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore
from tests.memory._pg import app_role_engine, sessionmaker_for

pytestmark = pytest.mark.integration

TENANT = f"t-b7-{uuid.uuid4().hex[:8]}"
CTX = TenantContext(tenant_id=TENANT, plan=PlanTier.PROFESSIONAL, api_key_id="k")


@pytest.fixture
async def app_sf(pg_url: str) -> AsyncIterator[async_sessionmaker]:
    admin = create_async_engine(pg_url)
    async with admin.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:t, :t, :e, 'professional', true) ON CONFLICT (id) DO NOTHING"
            ),
            {"t": TENANT, "e": f"{TENANT}@example.test"},
        )
    await admin.dispose()
    engine = await app_role_engine(pg_url, ["goals", "trigger_events", "trigger_dlq", "tenants"])
    try:
        yield sessionmaker_for(engine)
    finally:
        await engine.dispose()


@pytest.fixture
async def redis(redis_url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    import redis.asyncio as aioredis

    s = Settings(  # type: ignore[call-arg]
        _env_file=None, trigger_bus_block_ms=100, trigger_bus_claim_idle_ms=100
    )
    monkeypatch.setattr(bus, "get_settings", lambda: s)
    client = aioredis.from_url(redis_url, decode_responses=True)
    await client.delete("trigger:stream:goal", "trigger:stream:memory")
    try:
        yield client
    finally:
        await client.aclose()


class _PgGoalService:
    """GoalService.create_goal's contract, writing a real goals row with the
    execution_context the real adapter stores (lineage included)."""

    def __init__(self, sf: async_sessionmaker) -> None:
        self.sf = sf
        self.calls: list[dict[str, Any]] = []

    async def create_goal(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        goal_id = f"goal-{uuid.uuid4().hex[:12]}"
        ctx = {
            "source": "trigger",
            "trigger_idempotency_key": kw.get("idempotency_key"),
            "trigger_chain_depth": int(kw.get("trigger_chain_depth", 0) or 0),
            **({"source_trigger_id": kw["source_trigger_id"]} if kw.get("source_trigger_id") else {}),
        }
        await _insert_goal(self.sf, goal_id, ctx)
        return {"goal_id": goal_id}


async def _insert_goal(sf: async_sessionmaker, goal_id: str, ctx: dict[str, Any]) -> None:
    async with sf() as session, session.begin(), sqlalchemy_rls_context(session, TENANT):
        await session.execute(
            text(
                "INSERT INTO goals (id, tenant_id, goal_text, status, priority, "
                "execution_context) VALUES (:id, :t, 'g', 'planning', 'normal', "
                "CAST(:ctx AS json))"
            ),
            {"id": goal_id, "t": TENANT, "ctx": json.dumps(ctx)},
        )


async def _goal_ctx(sf: async_sessionmaker, goal_id: str) -> dict[str, Any]:
    async with sf() as session, sqlalchemy_rls_context(session, TENANT):
        raw = (
            await session.execute(
                text("SELECT execution_context FROM goals WHERE id = :g AND tenant_id = :t"),
                {"g": goal_id, "t": TENANT},
            )
        ).scalar_one()
    return raw if isinstance(raw, dict) else json.loads(raw)


async def _audit(sf: async_sessionmaker, trigger_id: str) -> list[tuple[Any, ...]]:
    async with sf() as session, sqlalchemy_rls_context(session, TENANT):
        rows = (
            await session.execute(
                text(
                    "SELECT goal_created, goal_id, skip_reason FROM trigger_events "
                    "WHERE tenant_id = :t AND trigger_id = :tr ORDER BY fired_at"
                ),
                {"t": TENANT, "tr": trigger_id},
            )
        ).fetchall()
    return [tuple(r) for r in rows]


async def _until(cond: Callable[[], Any], timeout: float = 20.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        result = cond()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.05)


async def test_goal_completed_chain_fires_once_and_never_loops(
    app_sf: async_sessionmaker, redis: Any
) -> None:
    store = ScheduleStore()
    goal_trigger = store.create(
        spec=TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED),
        tenant_ctx=CTX, goal_id="", goal_template="follow up", agent_id="",
    )
    memory_trigger = store.create(
        spec=TriggerSpec(trigger_type=TriggerType.MEMORY_CREATED),
        tenant_ctx=CTX, goal_id="", goal_template="review the learning",
    )
    goals = _PgGoalService(app_sf)
    dispatcher = TriggerDispatcher(goal_service=goals, db_session_factory=app_sf, redis=redis)
    consumers = [
        cls(trigger_store=store, dispatcher=dispatcher, redis=redis)
        for cls in (ChainTriggerConsumer, ChainTriggerConsumer, MemoryTriggerConsumer,
                    MemoryTriggerConsumer)
    ]
    tasks = [asyncio.create_task(c.start()) for c in consumers]

    async def completed(goal_id: str) -> None:
        lineage = lineage_from_context(await _goal_ctx(app_sf, goal_id))
        await bus.publish_trigger_event(
            redis,
            "goal.completed",
            build_chain_event(
                channel="goal.completed", tenant_id=TENANT, goal_id=goal_id,
                status="complete", trigger_chain_depth=lineage.depth,
                source_trigger_id=lineage.source_trigger_id,
            ),
        )

    async def memory_written(memory_id: str, goal_id: str) -> None:
        await bus.publish_trigger_event(
            redis,
            "memory.created",
            {"tenant_id": TENANT, "memory_id": memory_id, "memory_type": "success_pattern",
             "source_goal_id": goal_id},
        )

    try:
        # ── goal A (a user's goal) completes -> goal B, once ─────────────────
        await _insert_goal(app_sf, "goal-a-" + TENANT, {})
        await completed("goal-a-" + TENANT)
        await _until(lambda: len(goals.calls) == 1)
        goal_b = (await _audit(app_sf, goal_trigger))[0][1]
        b_ctx = await _goal_ctx(app_sf, goal_b)
        assert b_ctx["trigger_chain_depth"] == 1
        assert b_ctx["source_trigger_id"] == goal_trigger

        # ── goal B completes, relayed by two replicas: no re-fire ────────────
        await completed(goal_b)
        await completed(goal_b)

        async def b_refused() -> bool:
            return any(r[2] == "self_trigger" for r in await _audit(app_sf, goal_trigger))

        await _until(b_refused)

        # ── memory written by goal A -> goal C, once ─────────────────────────
        await memory_written("mem-a", "goal-a-" + TENANT)
        await _until(lambda: len(goals.calls) == 2)
        goal_c = goals.calls[1]
        assert goal_c["source_trigger_id"] == memory_trigger
        assert goal_c["trigger_chain_depth"] == 1
        # ...the same memory republished (an idempotent re-store): still once.
        await memory_written("mem-a", "goal-a-" + TENANT)

        # ── goal C writes its learning: lineage read from the goals row ──────
        goal_c_id = (await _audit(app_sf, memory_trigger))[0][1]
        await memory_written("mem-c", goal_c_id)

        async def c_refused() -> bool:
            return any(r[2] == "self_trigger" for r in await _audit(app_sf, memory_trigger))

        await _until(c_refused)
        await asyncio.sleep(0.5)  # anything still in flight would land now
    finally:
        for c in consumers:
            await c.stop()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert len(goals.calls) == 2  # B and C, nothing else
    fired = [r for r in await _audit(app_sf, goal_trigger) if r[0]]
    assert fired == [(True, goal_b, None)]
    fired_mem = [r for r in await _audit(app_sf, memory_trigger) if r[0]]
    assert fired_mem == [(True, goal_c_id, None)]
    for stream, group in (
        ("trigger:stream:goal", ChainTriggerConsumer.GROUP),
        ("trigger:stream:memory", MemoryTriggerConsumer.GROUP),
    ):
        assert (await redis.xpending(stream, group))["pending"] == 0
