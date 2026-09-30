"""TRG-21: memories written by worker-run goals publish ``memory.created``.

The worker built a bare ``LongTermMemoryStore()`` with no event Redis, so only
API-written memories published ``memory.created``; production goals run on
workers, so MEMORY_CREATED triggers effectively never fired. run_goal now builds
its store with ``_worker_long_term_memory()``, which binds the worker's async
Redis (as it already does for the HITL gateway).

Deliberately does not drive the whole run_goal task: that path opens the
worker's real Postgres/Redis connections.
"""

from __future__ import annotations

import inspect
import json
from typing import Any
from unittest.mock import AsyncMock

from app.memory.long_term import LongTermMemory
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.consumers.memory import MemoryTriggerConsumer
from app.triggers.models import TriggerSpec, TriggerType


class _Redis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self.streamed: list[tuple[str, dict[str, str]]] = []

    async def publish(self, channel: str, data: str) -> int:
        self.published.append((channel, data))
        return 1

    async def xadd(self, stream: str, fields: dict[str, str], **_: Any) -> str:
        self.streamed.append((stream, fields))  # TRG-18 durable trigger-bus copy
        return "1-0"


async def test_worker_memory_store_publishes_memory_created_and_fires(monkeypatch: Any) -> None:
    from app.scaling import tasks

    redis = _Redis()
    monkeypatch.setattr(tasks, "_worker_async_redis", lambda: redis)
    ltm = tasks._worker_long_term_memory()

    ctx = TenantContext(tenant_id="tenant-1", plan=PlanTier.FREE, api_key_id="w")
    mem = LongTermMemory(content="learned", source_goal_id="goal-m1", memory_type="domain_fact")
    await ltm.store_async(memory=mem, tenant_ctx=ctx)

    events = [json.loads(d) for c, d in redis.published if c == "memory.created"]
    assert [(e["tenant_id"], e["source_goal_id"]) for e in events] == [("tenant-1", "goal-m1")]

    # …and that payload drives the memory consumer.
    dispatcher = AsyncMock()
    dispatcher.resolve_tenant_plan = AsyncMock(return_value="free")
    store = AsyncMock()
    store.find_by_type_async = AsyncMock(
        return_value=[{"spec": TriggerSpec(trigger_type=TriggerType.MEMORY_CREATED)}]
    )
    consumer = MemoryTriggerConsumer(trigger_store=store, dispatcher=dispatcher)
    await consumer._handle({"data": json.dumps(events[0])})
    dispatcher.dispatch.assert_awaited_once()


def test_run_goal_builds_its_memory_store_with_the_event_redis() -> None:
    from app.scaling import tasks

    src = inspect.getsource(tasks.run_goal.run)
    assert "_worker_long_term_memory()" in src
    assert "LongTermMemoryStore()" not in src
