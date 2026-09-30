"""ORG-23: coordination frames fan out live to every subscriber, across replicas."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import fakeredis
import pytest

from app.coordination.live_bus import CoordinationLiveBus, live_channel


async def _next(iterator: AsyncIterator[dict[str, Any]], timeout: float = 2.0) -> dict[str, Any]:
    return await asyncio.wait_for(anext(iterator), timeout)


@pytest.mark.asyncio
async def test_in_process_bus_delivers_to_every_session_subscriber_only() -> None:
    bus = CoordinationLiveBus()
    async with (
        bus.subscribe("tenant", "session") as first,
        bus.subscribe("tenant", "session") as second,
        bus.subscribe("tenant", "other") as other_session,
        bus.subscribe("tenant-b", "session") as other_tenant,
    ):
        await bus.publish("tenant", "session", {"type": "message", "n": 1})
        assert await _next(first) == {"type": "message", "n": 1}
        assert await _next(second) == {"type": "message", "n": 1}
        for foreign in (other_session, other_tenant):
            with pytest.raises(TimeoutError):
                await _next(foreign, timeout=0.05)


@pytest.mark.asyncio
async def test_frames_cross_replicas_through_redis() -> None:
    server = fakeredis.FakeServer()
    redis_a = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    redis_b = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    replica_a = CoordinationLiveBus(lambda: redis_a)
    replica_b = CoordinationLiveBus(lambda: redis_b)
    async with replica_b.subscribe("tenant", "session") as subscriber:
        await replica_a.publish("tenant", "session", {"type": "message", "id": "m1"})
        assert await _next(subscriber) == {"type": "message", "id": "m1"}


def test_channel_is_tenant_and_session_scoped() -> None:
    assert live_channel("t1", "s1") != live_channel("t2", "s1")
    assert live_channel("t1", "s1") != live_channel("t1", "s2")
