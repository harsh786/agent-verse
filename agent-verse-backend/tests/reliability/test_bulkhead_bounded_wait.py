"""a08-F199-02 (owner decision): a full tenant bulkhead WAITS briefly for a slot.

It keeps 20 slots per tenant, but a step that finds them all taken is retried
with backoff + jitter for up to ``TENANT_BULKHEAD_WAIT_SECONDS`` (default 10 s)
and refused only after that, instead of failing at once.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.reliability.bulkhead import (
    BulkheadFullError,
    RedisBulkhead,
    acquire_with_wait,
    bulkhead_wait_seconds,
)
from tests.rpa._lease_redis import LeaseRedis


def test_default_wait_is_ten_seconds() -> None:
    assert bulkhead_wait_seconds() == 10.0


async def test_a_slot_freed_during_the_wait_is_taken() -> None:
    redis = LeaseRedis()
    holder = RedisBulkhead("t-w", 1, redis)
    assert await holder.acquire()
    waiter = RedisBulkhead("t-w", 1, redis)

    async def _free_soon() -> None:
        await asyncio.sleep(0.3)
        await holder.release()

    freer = asyncio.create_task(_free_soon())
    waited = await acquire_with_wait(waiter, wait_s=5)
    await freer
    assert 0.25 <= waited < 3
    await waiter.release()


async def test_refused_only_after_the_wait_expires() -> None:
    redis = LeaseRedis()
    holder = RedisBulkhead("t-w2", 1, redis)
    assert await holder.acquire()
    started = time.monotonic()
    with pytest.raises(BulkheadFullError, match=r"0\.4s"):
        await acquire_with_wait(RedisBulkhead("t-w2", 1, redis), wait_s=0.4)
    assert 0.35 <= time.monotonic() - started < 2
    await holder.release()


async def test_a_limit_check_error_is_not_waited_on() -> None:
    broken = SimpleNamespace(acquire=AsyncMock(side_effect=ConnectionError("down")))
    started = time.monotonic()
    with pytest.raises(ConnectionError):
        await acquire_with_wait(broken, wait_s=5)
    assert time.monotonic() - started < 0.5


async def test_semaphore_fallback_waits_too() -> None:
    sem = asyncio.Semaphore(1)
    await sem.acquire()
    loop = asyncio.get_running_loop()
    loop.call_later(0.2, sem.release)
    assert await acquire_with_wait(sem, wait_s=2) >= 0.15
    with pytest.raises(BulkheadFullError):
        await acquire_with_wait(sem, wait_s=0.1)


async def test_executor_step_waits_for_a_slot_instead_of_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState, StepResult, StepStatus
    from app.providers.fake import FakeProvider
    from app.tenancy.context import PlanTier, TenantContext

    monkeypatch.setattr("app.reliability.bulkhead.bulkhead_wait_seconds", lambda: 5.0)
    tenant = TenantContext(tenant_id="t-wx", plan=PlanTier.FREE, api_key_id="k")
    # Full twice, then a slot frees up: the step must run, not be refused.
    bulkhead = SimpleNamespace(
        acquire=AsyncMock(side_effect=[False, False, True]), release=AsyncMock()
    )
    executor = FakeProvider(responses=["real output"])
    graph = AgentGraph(
        planner=FakeProvider(responses=['{"steps": ["do it"]}']),
        executor=executor,
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        bulkhead_registry=SimpleNamespace(get_bulkhead=MagicMock(return_value=bulkhead)),
    )
    state = AgentState(goal="g", tenant_ctx=tenant)
    state.goal_id = "goal-w"
    state.steps.append(StepResult(description="do it", status=StepStatus.RUNNING))

    assert await graph._execute_step("do it", state, tenant) == "real output"
    assert bulkhead.acquire.await_count == 3
    bulkhead.release.assert_awaited_once()
