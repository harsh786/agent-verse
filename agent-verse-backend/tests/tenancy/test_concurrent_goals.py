"""Tests for concurrent goal limit enforcement."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.limits import (
    PlanLimitExceededError,
    check_and_increment_concurrent_goals,
    decrement_concurrent_goals,
)

FREE = TenantContext(tenant_id="tenant-free", plan=PlanTier.FREE, api_key_id="k1")
ENTERPRISE = TenantContext(tenant_id="tenant-ent", plan=PlanTier.ENTERPRISE, api_key_id="k2")


def make_redis(current_value: int) -> AsyncMock:
    """Create a mock Redis client returning the given current counter value.

    The atomic Lua implementation uses redis.eval(script, numkeys, key, limit, ttl).
    The side_effect simulates the Lua: INCR key, check if over limit, DECR if so.
    """
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=str(current_value).encode())
    redis.incr = AsyncMock(return_value=current_value + 1)
    redis.decr = AsyncMock(return_value=max(current_value - 1, 0))
    redis.expire = AsyncMock(return_value=True)

    # Simulate Lua atomics: INCR → check > limit → DECR+return 0 if so
    async def _eval_lua(script: str, numkeys: int, key: str, limit: int, ttl: int) -> int:
        new_val = current_value + 1
        if new_val > limit:
            return 0  # Over limit — atomically DECR'd back in real Lua
        return new_val

    redis.eval = AsyncMock(side_effect=_eval_lua)
    return redis


# ── Test 1: raises when at limit ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_raises_when_at_concurrent_limit():
    """Free plan has limit=2; raises when counter == 2."""
    redis = make_redis(2)  # already at limit — INCR would make it 3 > 2
    with pytest.raises(PlanLimitExceededError, match="Concurrent goal limit"):
        await check_and_increment_concurrent_goals(FREE, redis)


# ── Test 2: passes when under limit ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_passes_when_under_limit():
    """Counter is 1 (< limit 2) — atomic eval should succeed."""
    redis = make_redis(1)
    await check_and_increment_concurrent_goals(FREE, redis)
    # Lua eval performs INCR+EXPIRE atomically; direct incr/expire not called
    redis.eval.assert_awaited_once()


# ── Test 3: decrement decrements counter ─────────────────────────────────────

@pytest.mark.asyncio
async def test_decrement_concurrent_goals():
    redis = make_redis(3)
    await decrement_concurrent_goals(FREE.tenant_id, redis)
    redis.decr.assert_awaited_once_with(f"concurrent_goals:{FREE.tenant_id}")


# ── Test 4: decrement safe when counter is 0 ─────────────────────────────────

@pytest.mark.asyncio
async def test_decrement_safe_when_counter_is_zero():
    """When counter is 0, decr should NOT be called."""
    redis = make_redis(0)
    await decrement_concurrent_goals(FREE.tenant_id, redis)
    redis.decr.assert_not_awaited()


# ── Test 5: Redis unavailable — exception swallowed ──────────────────────────

@pytest.mark.asyncio
async def test_redis_unavailable_swallows_exception():
    """When Redis raises, both check_and_increment and decrement silently pass."""
    redis = AsyncMock()
    redis.eval = AsyncMock(side_effect=ConnectionError("redis down"))
    redis.decr = AsyncMock(side_effect=ConnectionError("redis down"))

    # Should not raise — goal is allowed when Redis unavailable
    await check_and_increment_concurrent_goals(FREE, redis)
    await decrement_concurrent_goals(FREE.tenant_id, redis)


# ── Test 6: tenant isolation (different keys) ─────────────────────────────────

@pytest.mark.asyncio
async def test_tenant_isolation():
    """Each tenant uses a separate Redis key."""
    redis_free = make_redis(0)
    redis_ent = make_redis(0)

    await check_and_increment_concurrent_goals(FREE, redis_free)
    await check_and_increment_concurrent_goals(ENTERPRISE, redis_ent)

    free_key = f"concurrent_goals:{FREE.tenant_id}"
    ent_key = f"concurrent_goals:{ENTERPRISE.tenant_id}"

    # Each redis mock eval was called with its own tenant's key (3rd positional arg)
    redis_free.eval.assert_awaited()
    redis_ent.eval.assert_awaited()
    assert redis_free.eval.await_args.args[2] == free_key
    assert redis_ent.eval.await_args.args[2] == ent_key
    assert free_key != ent_key

