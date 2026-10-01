"""RATE-04/05/06 on a real Redis: the atomic Lua lease path, multi-replica safe.

Every "replica" is its own client connection to the same Redis testcontainer;
the limit holds under concurrent acquires, double releases never free another
goal's slot, and expiry uses Redis server time (TIME), not replica clocks.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import redis.asyncio as aioredis

from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.limits import (
    PlanLimitExceededError,
    check_and_increment_concurrent_goals,
    concurrent_goal_lease_key,
    decrement_concurrent_goals,
    renew_concurrent_goal_lease,
)

pytestmark = pytest.mark.integration


async def _clients(redis_url: str, n: int) -> list[Any]:
    return [aioredis.from_url(redis_url, decode_responses=True) for _ in range(n)]


async def test_concurrent_acquires_across_replicas_admit_exactly_the_limit(
    redis_url: str,
) -> None:
    ctx = TenantContext(tenant_id="t-race", plan=PlanTier.STARTER, api_key_id="k")  # limit 5
    replicas = await _clients(redis_url, 8)
    try:
        await replicas[0].delete(concurrent_goal_lease_key("t-race"))
        results = await asyncio.gather(
            *[
                check_and_increment_concurrent_goals(ctx, replicas[i % 8], goal_id=f"g{i}")
                for i in range(40)
            ],
            return_exceptions=True,
        )
        admitted = [r for r in results if r is None]
        refused = [r for r in results if isinstance(r, PlanLimitExceededError)]
        assert len(admitted) == 5 and len(refused) == 35
        assert await replicas[0].zcard(concurrent_goal_lease_key("t-race")) == 5
    finally:
        for c in replicas:
            await c.aclose()


async def test_concurrent_double_releases_free_only_their_own_slot(redis_url: str) -> None:
    ctx = TenantContext(tenant_id="t-rel", plan=PlanTier.FREE, api_key_id="k")  # limit 2
    api, worker = await _clients(redis_url, 2)
    key = concurrent_goal_lease_key("t-rel")
    try:
        await api.delete(key)
        await check_and_increment_concurrent_goals(ctx, api, goal_id="cancelled")
        await check_and_increment_concurrent_goals(ctx, worker, goal_id="running")
        released = await asyncio.gather(
            decrement_concurrent_goals("t-rel", api, goal_id="cancelled"),
            decrement_concurrent_goals("t-rel", worker, goal_id="cancelled"),
        )
        assert sorted(released) == [False, True]
        assert await api.zrange(key, 0, -1) == ["running"]
        await check_and_increment_concurrent_goals(ctx, api, goal_id="next")
        with pytest.raises(PlanLimitExceededError):
            await check_and_increment_concurrent_goals(ctx, worker, goal_id="over")
    finally:
        await api.aclose()
        await worker.aclose()


async def test_expired_lease_is_reclaimed_by_server_time(redis_url: str) -> None:
    ctx = TenantContext(tenant_id="t-exp", plan=PlanTier.FREE, api_key_id="k")
    (r,) = await _clients(redis_url, 1)
    key = concurrent_goal_lease_key("t-exp")
    try:
        await r.delete(key)
        sec, usec = await r.time()
        past_ms = sec * 1000 + usec // 1000 - 1000
        await r.zadd(key, {"dead-1": past_ms, "dead-2": past_ms})
        await check_and_increment_concurrent_goals(ctx, r, goal_id="g1")
        assert await r.zrange(key, 0, -1) == ["g1"]
        # The key itself expires with its longest lease (no unbounded keys).
        assert await r.pttl(key) > 0
    finally:
        await r.aclose()


async def test_idempotent_acquire_and_renew_on_real_redis(redis_url: str) -> None:
    ctx = TenantContext(tenant_id="t-idem", plan=PlanTier.FREE, api_key_id="k")
    (r,) = await _clients(redis_url, 1)
    key = concurrent_goal_lease_key("t-idem")
    try:
        await r.delete(key)
        await check_and_increment_concurrent_goals(ctx, r, goal_id="g1")
        await check_and_increment_concurrent_goals(ctx, r, goal_id="g1")
        assert await r.zcard(key) == 1
        before = await r.zscore(key, "g1")
        assert await renew_concurrent_goal_lease("t-idem", r, goal_id="g1", lease_seconds=10**6)
        assert await r.zscore(key, "g1") > before
        assert await r.pttl(key) > 10**8
        assert not await renew_concurrent_goal_lease(
            "t-idem", r, goal_id="nope", lease_seconds=60
        )
        assert await r.zscore(key, "nope") is None
    finally:
        await r.aclose()
