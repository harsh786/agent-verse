"""Tests for concurrent goal limit enforcement."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.limits import (
    PlanLimitExceededError,
    check_and_increment_concurrent_goals,
    decrement_concurrent_goals,
)

FREE = TenantContext(tenant_id="tenant-free", plan=PlanTier.FREE, api_key_id="k1")
ENTERPRISE = TenantContext(tenant_id="tenant-ent", plan=PlanTier.ENTERPRISE, api_key_id="k2")


def make_redis(current_value: int) -> AsyncMock:
    """Create a mock Redis client returning the given current counter value."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=str(current_value).encode())
    redis.incr = AsyncMock(return_value=current_value + 1)
    redis.decr = AsyncMock(return_value=max(current_value - 1, 0))
    redis.expire = AsyncMock(return_value=True)
    return redis


# ── Test 1: raises when at limit ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_raises_when_at_concurrent_limit():
    """Free plan has limit=2; raises when counter == 2."""
    redis = make_redis(2)  # already at limit
    with pytest.raises(PlanLimitExceededError, match="Concurrent goal limit"):
        await check_and_increment_concurrent_goals(FREE, redis)


# ── Test 2: passes when under limit ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_passes_when_under_limit():
    """Counter is 1 (< limit 2) — should increment without raising."""
    redis = make_redis(1)
    await check_and_increment_concurrent_goals(FREE, redis)
    redis.incr.assert_awaited_once()
    redis.expire.assert_awaited_once()


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
    redis.get = AsyncMock(side_effect=ConnectionError("redis down"))
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

    # Each redis mock was called with its own tenant's key
    redis_free.get.assert_awaited_with(free_key)
    redis_ent.get.assert_awaited_with(ent_key)
    assert free_key != ent_key
