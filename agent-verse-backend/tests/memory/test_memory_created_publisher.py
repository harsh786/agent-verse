"""Regression: MEMORY_CREATED triggers could never fire — MemoryTriggerConsumer
subscribed to ``memory.created`` but no code published it."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.memory.long_term import LongTermMemory, LongTermMemoryStore
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.consumers.memory import MemoryTriggerConsumer

CTX = TenantContext(tenant_id="t-mem", plan=PlanTier.FREE, api_key_id="k")


async def test_store_async_publishes_memory_created_and_drives_the_consumer() -> None:
    ltm = LongTermMemoryStore()
    redis = AsyncMock()
    ltm.set_event_redis(redis)
    mem = LongTermMemory(content="prefers jira", source_goal_id="g1", memory_type="tool_preference")
    with patch("app.memory.long_term._GUARDRAILS_AVAILABLE", False):
        await ltm.store_async(memory=mem, tenant_ctx=CTX)

    (call,) = [c for c in redis.publish.await_args_list if c.args[0] == "memory.created"]
    spec = SimpleNamespace(memory_type="tool_preference")
    store = SimpleNamespace(find_by_type_async=AsyncMock(return_value=[{"spec": spec}]))
    dispatcher = SimpleNamespace(dispatch=AsyncMock())
    consumer = MemoryTriggerConsumer(trigger_store=store, dispatcher=dispatcher)
    await consumer._handle({"type": "message", "channel": "memory.created", "data": call.args[1]})
    dispatcher.dispatch.assert_awaited_once()


async def test_no_redis_publishes_nothing() -> None:
    ltm = LongTermMemoryStore()
    mem = LongTermMemory(content="x", source_goal_id="g", memory_type="domain_fact")
    with patch("app.memory.long_term._GUARDRAILS_AVAILABLE", False):
        await ltm.store_async(memory=mem, tenant_ctx=CTX)  # must not raise


async def test_failed_insert_publishes_no_memory_created() -> None:
    """MEM-05: the event used to go out before the INSERT; a rolled-back write
    left subscribers acting on a memory that does not exist."""
    import pytest

    from app.memory.long_term import LongTermMemoryUnavailableError

    class _BrokenDb:
        def __call__(self) -> object:
            raise ConnectionError("db down")

    ltm = LongTermMemoryStore()
    redis = AsyncMock()
    ltm.set_event_redis(redis)
    mem = LongTermMemory(content="x", source_goal_id="g", memory_type="domain_fact")
    with (
        patch("app.memory.long_term._GUARDRAILS_AVAILABLE", False),
        pytest.raises(LongTermMemoryUnavailableError),
    ):
        await ltm.store_async(memory=mem, tenant_ctx=CTX, db=_BrokenDb())
    assert not [c for c in redis.publish.await_args_list if c.args[0] == "memory.created"]
    assert not [c for c in redis.xadd.await_args_list if "memory" in str(c.args[:1])]
