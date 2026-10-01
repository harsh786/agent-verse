"""COORD-STRATEGIES-RUNNER: the StrategyRunner budget reservation with the Redis controller.

``_strategy_budget_reserver`` (and the guarded-completion preflight) call
``controller.ahas_remaining_budget``. Only the in-memory ``CostController`` had
it; the production ``RedisCostController`` did not, so with Redis wired every
DISTRIBUTED goal (supervisor, debate, the coordination patterns, voyager) was
refused with ``budget_reservation_failed`` — found by driving such goals end
to end through the booted app.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import fakeredis.aioredis
import pytest

from app.governance.cost import BudgetConfig, RedisCostController
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-budget", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _controller(redis: Any) -> RedisCostController:
    ctrl = RedisCostController(redis)
    ctrl.configure_tenant_budget(
        CTX.tenant_id, BudgetConfig(per_goal_usd=1.0, per_tenant_daily_usd=2.0)
    )
    return ctrl


@pytest.mark.asyncio
async def test_redis_controller_reports_remaining_daily_budget() -> None:
    ctrl = _controller(fakeredis.aioredis.FakeRedis())
    assert await ctrl.ahas_remaining_budget(tenant_ctx=CTX) is True
    await ctrl._redis.set(ctrl._daily_key(CTX.tenant_id), "1.8")  # what charges accrued
    assert await ctrl.ahas_remaining_budget(tenant_ctx=CTX) is True  # 1.8 < 2.0
    await ctrl._redis.set(ctrl._daily_key(CTX.tenant_id), "2.0")
    assert await ctrl.ahas_remaining_budget(tenant_ctx=CTX) is False


@pytest.mark.asyncio
async def test_redis_outage_is_not_reported_as_budget_left() -> None:
    class _Down:
        async def get(self, *_: Any) -> Any:
            raise ConnectionError("redis down")

    with pytest.raises(ConnectionError):
        await _controller(_Down()).ahas_remaining_budget(tenant_ctx=CTX)


@pytest.mark.asyncio
async def test_strategy_runner_reservation_admits_with_the_redis_controller() -> None:
    from app.main import _strategy_budget_reserver

    state = SimpleNamespace(redis_cost_controller=_controller(fakeredis.aioredis.FakeRedis()))
    reserve = _strategy_budget_reserver(state)
    request = SimpleNamespace(tenant_id=CTX.tenant_id)
    assert await reserve(request, None) is True
    await state.redis_cost_controller._redis.set(
        state.redis_cost_controller._daily_key(CTX.tenant_id), "5"
    )
    assert await reserve(request, None) is False
