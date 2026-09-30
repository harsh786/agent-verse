"""TRG-18 against a real Redis 7: stream XADD, consumer groups, XACK, XAUTOCLAIM.

The unit tests use fakeredis (which does not honour BLOCK); this checks the
reader against real Redis semantics: a blocking XREADGROUP, the Redis 7
three-element XAUTOCLAIM reply, and redelivery of a crashed replica's entry.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.triggers import bus
from app.triggers.consumers.chain import ChainTriggerConsumer, build_chain_event
from app.triggers.models import TriggerSpec, TriggerType

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    import redis.asyncio as aioredis
    from testcontainers.redis import RedisContainer

    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        trigger_bus_block_ms=200,
        trigger_bus_claim_idle_ms=100,
        trigger_bus_max_deliveries=3,
    )
    monkeypatch.setattr(bus, "get_settings", lambda: s)
    with RedisContainer("redis:7-alpine") as container:
        url = f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"
        client = aioredis.from_url(url, decode_responses=True)
        try:
            yield client
        finally:
            await client.aclose()


class _Store:
    async def find_by_type_async(self, trigger_type: str, tenant_id: str = "", **_: Any) -> list:
        return [{"spec": TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED)}]


async def _until(cond: Any, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.02)


async def test_real_redis_backlog_crash_redelivery_and_ack(redis: Any) -> None:
    stream, group = "trigger:stream:goal", ChainTriggerConsumer.GROUP

    async def publish(goal_id: str) -> None:
        await bus.publish_trigger_event(
            redis,
            "goal.completed",
            build_chain_event(channel="goal.completed", tenant_id="t1", goal_id=goal_id),
        )

    # Published before any consumer exists, then a replica reads and crashes.
    await publish("g-backlog")
    await publish("g-crashed")
    await bus.TriggerStreamReader(redis, stream=stream, group=group).ensure_group()
    await redis.xreadgroup(group, "dead-replica", {stream: ">"}, count=1)  # g-backlog in flight

    disp = AsyncMock()
    disp.resolve_tenant_plan = AsyncMock(return_value="free")
    consumer = ChainTriggerConsumer(trigger_store=_Store(), dispatcher=disp, redis=redis)
    task = asyncio.create_task(consumer.start())
    try:
        await _until(lambda: disp.dispatch.await_count >= 2)
        await publish("g-live")  # arrives while XREADGROUP is blocking
        await _until(lambda: disp.dispatch.await_count >= 3)
        await asyncio.sleep(0.3)
    finally:
        await consumer.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    ids = sorted(c.kwargs["source_goal_id"] for c in disp.dispatch.await_args_list)
    assert ids == ["g-backlog", "g-crashed", "g-live"]
    assert (await redis.xpending(stream, group))["pending"] == 0
    assert await redis.xlen(stream) == 3
