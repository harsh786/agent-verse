"""The tenant bulkhead on a real Redis (Lua leases), across "replicas" and task loops.

a08-F199-01/03/04.
"""

from __future__ import annotations

import asyncio

import pytest

from app.reliability.bulkhead import LoopLocalRedis, RedisBulkhead, RedisBulkheadRegistry

pytestmark = pytest.mark.integration


async def test_two_replicas_share_the_cap_and_a_dead_holder_frees_itself(
    redis_url: str,
) -> None:
    import redis.asyncio as aioredis

    a, b = aioredis.from_url(redis_url), aioredis.from_url(redis_url)
    try:
        await a.delete("bulkhead_leases:t-int")
        replica_a = RedisBulkheadRegistry(redis=a, default_max_concurrent=2)
        replica_b = RedisBulkheadRegistry(redis=b, default_max_concurrent=2)
        h1 = replica_a.get_bulkhead("t-int")
        h2 = replica_b.get_bulkhead("t-int")
        h3 = replica_b.get_bulkhead("t-int")
        assert isinstance(h1, RedisBulkhead)
        assert isinstance(h2, RedisBulkhead)
        assert isinstance(h3, RedisBulkhead)
        assert await h1.acquire() and await h2.acquire()
        assert not await h3.acquire()
        assert await replica_a.available_slots("t-int") == 0

        # Replica A crashes holding h1 with a short lease: nobody renews it.
        crashed = RedisBulkhead("t-int", 2, a, lease_s=0.3)
        await h1.release()
        assert await crashed.acquire()
        assert crashed._keepalive is not None
        crashed._keepalive.cancel()
        assert not await h3.acquire()
        await asyncio.sleep(0.5)
        assert await h3.acquire()  # the dead holder's slot expired

        # A step longer than its lease keeps the slot (renewed).
        await h2.release()
        await h3.release()
        long_step = RedisBulkhead("t-int", 1, a, lease_s=0.3)
        assert await long_step.acquire()
        await asyncio.sleep(0.8)
        assert not await RedisBulkhead("t-int", 1, b, lease_s=0.3).acquire()
        await long_step.release()
        assert await replica_b.available_slots("t-int") == 2
    finally:
        await a.aclose()
        await b.aclose()


def test_worker_registry_is_process_wide_and_closes_its_loop_clients(
    redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db.session import run_in_fresh_loop
    from app.scaling import tasks

    monkeypatch.setattr(tasks, "REDIS_URL", redis_url)
    monkeypatch.setattr(tasks, "_WORKER_BULKHEAD", None)
    first, second = tasks._worker_bulkhead_registry(), tasks._worker_bulkhead_registry()
    assert first is second  # one per process, not one (plus a client) per goal
    proxy = first._redis
    assert isinstance(proxy, LoopLocalRedis)

    clients: list[object] = []

    async def _one_goal() -> None:
        bh = first.get_bulkhead("t-worker")
        assert await bh.acquire()
        clients.append(proxy._client())
        await bh.release()

    run_in_fresh_loop(_one_goal())
    run_in_fresh_loop(_one_goal())
    assert len(clients) == 2 and clients[0] is not clients[1]  # one per task loop
    # Each loop's client was closed at that loop's teardown.
    for client in clients:
        pool = client.connection_pool  # type: ignore[attr-defined]
        assert not pool._in_use_connections
        assert all(not c.is_connected for c in pool._available_connections)
