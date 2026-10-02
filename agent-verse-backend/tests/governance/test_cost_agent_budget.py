"""COST-02: per-agent daily budgets are enforced, not just stored.

``PUT /costs/budgets`` accepted ``per_agent_daily_usd`` and persisted it, but no
check ever read it: an agent could spend the tenant's whole daily budget. The
charge now carries the agent (explicitly, or from the running goal's
``cost_agent_scope``) and a third counter ``cost:agent_daily:<tenant>:<agent>:<day>``
is checked and incremented in the same atomic step as the goal/tenant counters.
"""

from __future__ import annotations

import uuid

import pytest

from app.governance.cost import (
    BudgetConfig,
    CostController,
    RedisCostController,
    cost_agent_scope,
)
from app.tenancy.context import PlanTier, TenantContext


def _ctx(tid: str) -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.PROFESSIONAL, api_key_id="k")


CFG = BudgetConfig(
    per_goal_usd=100.0, per_tenant_daily_usd=100.0, per_agent_daily_usd={"agent-a": 1.0}
)


async def test_in_memory_agent_cap_denies_while_tenant_cap_has_room() -> None:
    cc = CostController(CFG)
    t = _ctx("t-agent")
    assert await cc.check_and_record(goal_id="g1", cost_usd=0.6, tenant_ctx=t, agent_id="agent-a")
    assert not await cc.check_and_record(
        goal_id="g2", cost_usd=0.6, tenant_ctx=t, agent_id="agent-a"
    )
    # Another agent (no cap) and the tenant total are unaffected.
    assert await cc.check_and_record(goal_id="g3", cost_usd=5.0, tenant_ctx=t, agent_id="agent-b")


async def test_running_goals_agent_scope_is_charged_without_an_explicit_agent() -> None:
    cc = CostController(CFG)
    t = _ctx("t-scope")
    with cost_agent_scope("agent-a"):
        assert await cc.check_and_record(goal_id="g", cost_usd=0.9, tenant_ctx=t)
        assert not await cc.check_and_record(goal_id="g", cost_usd=0.2, tenant_ctx=t)
    assert await cc.check_and_record(goal_id="g", cost_usd=0.2, tenant_ctx=t)


def test_worker_run_charges_the_goals_agent() -> None:
    from app.governance.cost import _COST_AGENT
    from app.scaling.tasks import _charged_to_agent, _run_async

    async def _charge() -> str:
        return _COST_AGENT.get()

    assert _run_async(_charged_to_agent(_charge(), "agent-w")) == "agent-w"


async def test_in_process_lua_emulation_enforces_the_agent_cap() -> None:
    from app.main import _FakeRedis

    cc = RedisCostController(_FakeRedis(), per_tenant_config={"t-emu": CFG})
    t = _ctx("t-emu")
    assert await cc.check_and_record(goal_id="g", cost_usd=0.9, tenant_ctx=t, agent_id="agent-a")
    assert not await cc.check_and_record(
        goal_id="g", cost_usd=0.2, tenant_ctx=t, agent_id="agent-a"
    )


@pytest.mark.integration
async def test_redis_lua_agent_cap_denies_while_tenant_cap_has_room(redis_url: str) -> None:
    import redis.asyncio as aioredis

    r = aioredis.from_url(redis_url)
    tid = f"t-{uuid.uuid4().hex[:8]}"
    try:
        cc = RedisCostController(r, per_tenant_config={tid: CFG})
        t = _ctx(tid)
        assert await cc.check_and_record_async(
            goal_id="g1", cost_usd=0.6, tenant_ctx=t, agent_id="agent-a"
        )
        assert not await cc.check_and_record_async(
            goal_id="g2", cost_usd=0.6, tenant_ctx=t, agent_id="agent-a"
        )
        assert await cc.check_and_record_async(
            goal_id="g3", cost_usd=5.0, tenant_ctx=t, agent_id="agent-b"
        )
        # The denied charge incremented nothing (check-then-increment).
        assert await cc.get_tenant_cost_today(t) == pytest.approx(5.6)
    finally:
        await r.aclose()
