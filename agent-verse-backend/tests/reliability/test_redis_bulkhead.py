"""Tests for Redis-backed distributed bulkhead (RedisBulkhead + RedisBulkheadRegistry).

a08-F199-01/03/04: slots are renewed leases (a crashed holder's slot expires,
a long step keeps its slot), release errors are logged, ``available_slots`` is
the fleet-wide count, and the Redis-down fallback is one process-wide limit.
"""

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from app.reliability.bulkhead import (
    LocalSlotCounter,
    RedisBulkhead,
    RedisBulkheadRegistry,
)
from tests.rpa._lease_redis import LeaseRedis


async def test_redis_bulkhead_acquire_success() -> None:
    bh = RedisBulkhead("tenant-1", max_concurrent=5, redis=LeaseRedis())
    assert await bh.acquire() is True
    await bh.release()


async def test_redis_bulkhead_acquire_at_limit_returns_false() -> None:
    redis = LeaseRedis()
    holders = [RedisBulkhead("tenant-1", max_concurrent=2, redis=redis) for _ in range(3)]
    assert await holders[0].acquire() is True
    assert await holders[1].acquire() is True
    assert await holders[2].acquire() is False
    await holders[0].release()
    assert await holders[2].acquire() is True
    await holders[1].release()
    await holders[2].release()


async def test_release_frees_exactly_its_own_lease() -> None:
    redis = LeaseRedis()
    a = RedisBulkhead("tenant-1", max_concurrent=5, redis=redis)
    b = RedisBulkhead("tenant-1", max_concurrent=5, redis=redis)
    await a.acquire()
    await b.acquire()
    await a.release()
    await a.release()  # a second release is a no-op, never another holder's slot
    assert await b.available_slots() == 4
    await b.release()
    assert await b.available_slots() == 5


async def test_redis_bulkhead_context_manager_releases_on_exception() -> None:
    redis = LeaseRedis()
    bh = RedisBulkhead("tenant-1", max_concurrent=1, redis=redis)
    with pytest.raises(ValueError):
        async with bh:
            raise ValueError("test error")
    assert await bh.available_slots() == 1


async def test_a_crashed_holders_slot_expires_even_under_traffic() -> None:
    """The old counter re-armed its TTL on every acquire: a leaked slot never expired."""
    redis = LeaseRedis()
    crashed = RedisBulkhead("tenant-1", max_concurrent=1, redis=redis, lease_s=0.2)
    assert await crashed.acquire() is True
    assert crashed._keepalive is not None
    crashed._keepalive.cancel()  # the process died: nobody renews or releases

    other = RedisBulkhead("tenant-1", max_concurrent=1, redis=redis, lease_s=0.2)
    assert await other.acquire() is False  # traffic while the lease is live
    await asyncio.sleep(0.3)
    assert await other.acquire() is True  # freed by expiry, not by a TTL reset
    await other.release()


async def test_a_step_longer_than_the_lease_keeps_its_slot() -> None:
    redis = LeaseRedis()
    long_step = RedisBulkhead("tenant-1", max_concurrent=1, redis=redis, lease_s=0.3)
    assert await long_step.acquire() is True
    await asyncio.sleep(0.7)  # > 2 leases: renewed in the background
    other = RedisBulkhead("tenant-1", max_concurrent=1, redis=redis, lease_s=0.3)
    assert await other.acquire() is False
    await long_step.release()
    assert await other.acquire() is True
    await other.release()


async def test_a_failed_release_is_logged_not_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = LeaseRedis()
    bh = RedisBulkhead("tenant-1", max_concurrent=1, redis=redis)
    await bh.acquire()
    redis.fail = True
    with caplog.at_level(logging.WARNING, logger="app.reliability.bulkhead"):
        await bh.release()
    assert "bulkhead_release_failed" in caplog.text


async def test_redis_bulkhead_denies_when_redis_unavailable_without_fallback() -> None:
    redis = LeaseRedis()
    redis.fail = True
    bh = RedisBulkhead("tenant-1", max_concurrent=5, redis=redis)
    assert await bh.acquire() is False


async def test_redis_bulkhead_degrades_to_local_limit_when_redis_unavailable() -> None:
    redis = AsyncMock()
    redis.eval = AsyncMock(side_effect=ConnectionError("Redis down"))
    local = asyncio.Semaphore(1)

    first = RedisBulkhead("tenant-1", max_concurrent=5, redis=redis, fallback=local)
    second = RedisBulkhead("tenant-1", max_concurrent=5, redis=redis, fallback=local)
    assert await first.acquire() is True
    assert await second.acquire() is False  # local limit (1) reached
    await first.release()
    assert await second.acquire() is True
    await second.release()
    assert not local.locked()


async def test_registry_fallback_is_one_process_wide_limit() -> None:
    """a08-F199-04: two registries (two worker goals) share one fallback limit."""
    down = LeaseRedis()
    down.fail = True
    r1 = RedisBulkheadRegistry(redis=down, default_max_concurrent=1)
    r2 = RedisBulkheadRegistry(redis=down, default_max_concurrent=1)
    a, b = r1.get_bulkhead("tenant-fb"), r2.get_bulkhead("tenant-fb")
    assert isinstance(a, RedisBulkhead) and isinstance(b, RedisBulkhead)
    assert isinstance(a._fallback, LocalSlotCounter)
    assert await a.acquire() is True
    assert await b.acquire() is False
    await a.release()
    assert await b.acquire() is True
    await b.release()


async def test_registry_available_slots_reads_the_fleet_wide_count() -> None:
    """a08-F199-01: it read the process-local registry (always the full limit)."""
    redis = LeaseRedis()
    registry = RedisBulkheadRegistry(redis=redis, default_max_concurrent=3)
    other_replica = RedisBulkheadRegistry(redis=redis, default_max_concurrent=3)
    held = other_replica.get_bulkhead("tenant-x")
    assert isinstance(held, RedisBulkhead)
    await held.acquire()
    assert await registry.available_slots("tenant-x") == 2
    await held.release()
    assert await registry.available_slots("tenant-x") == 3
    assert not hasattr(RedisBulkhead, "available_slots_sync")


async def test_redis_bulkhead_registry_uses_redis_when_available() -> None:
    registry = RedisBulkheadRegistry(redis=AsyncMock(), default_max_concurrent=10)
    assert isinstance(registry.get_bulkhead("tenant-x"), RedisBulkhead)


async def test_redis_bulkhead_registry_falls_back_to_semaphore() -> None:
    registry = RedisBulkheadRegistry(redis=None, default_max_concurrent=10)
    assert isinstance(registry.get_bulkhead("tenant-x"), asyncio.Semaphore)
    assert await registry.available_slots("tenant-x") == 10


def test_agentgraph_accepts_bulkhead_registry() -> None:
    import inspect

    from app.agent.graph import AgentGraph

    sig = inspect.signature(AgentGraph.__init__)
    assert "bulkhead_registry" in sig.parameters
