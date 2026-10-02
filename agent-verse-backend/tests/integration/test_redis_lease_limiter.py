"""RedisLeaseLimiter against real Redis (Lua): the cap holds across processes,
releases free slots, and a crashed holder's lease expires on its own."""

from __future__ import annotations

import asyncio

import pytest

from app.reliability.bulkhead import RedisLeaseLimiter

pytestmark = pytest.mark.integration


async def test_lease_cap_shared_by_two_clients(redis_url: str) -> None:
    import redis.asyncio as aioredis

    a, b = aioredis.from_url(redis_url), aioredis.from_url(redis_url)
    try:
        la, lb = RedisLeaseLimiter(a), RedisLeaseLimiter(b)
        key = "code_exec:leases:tenant-x"
        assert await la.try_acquire(key, "m1", limit=2, lease_s=30)
        assert await lb.try_acquire(key, "m2", limit=2, lease_s=30)
        assert not await la.try_acquire(key, "m3", limit=2, lease_s=30)
        # Re-acquiring a held lease renews it rather than taking a new slot.
        assert await lb.try_acquire(key, "m2", limit=2, lease_s=30)
        assert sorted(await la.members(key)) == ["m1", "m2"]
        await la.release(key, "m1")
        assert await lb.try_acquire(key, "m3", limit=2, lease_s=30)

        # A holder that dies without releasing frees its slot when the lease expires.
        short = "rpa:leases:tenant-y"
        assert await la.try_acquire(short, "dead", limit=1, lease_s=0.3)
        assert not await lb.try_acquire(short, "live", limit=1, lease_s=30)
        assert await la.refresh(short, "dead", lease_s=0.3)
        await asyncio.sleep(0.5)
        assert not await la.refresh(short, "dead", lease_s=30)
        assert await lb.try_acquire(short, "live", limit=1, lease_s=30)
    finally:
        await a.aclose()
        await b.aclose()
