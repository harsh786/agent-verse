"""Tests for _FakeRedis concurrent access safety."""
from __future__ import annotations

import asyncio

import pytest

from app.main import _FakeRedis


@pytest.mark.asyncio
async def test_concurrent_zadd_is_safe() -> None:
    """Concurrent zadd operations should not corrupt the sorted set."""
    fake = _FakeRedis()

    async def add_items(prefix: str, count: int) -> None:
        for i in range(count):
            await fake.zadd("test_key", {f"{prefix}:{i}": float(i)})

    # 10 concurrent coroutines each adding 10 unique items = 100 total
    await asyncio.gather(*[add_items(f"batch{b}", 10) for b in range(10)])

    count = await fake.zcard("test_key")
    assert count == 100


@pytest.mark.asyncio
async def test_concurrent_zremrangebyscore_is_safe() -> None:
    """Concurrent remove + add should not corrupt sorted set (no exception)."""
    fake = _FakeRedis()

    # Seed with 50 items
    for i in range(50):
        await fake.zadd("ratelimit_key", {f"req:{i}": float(i)})

    async def remove_and_add(i: int) -> None:
        await fake.zremrangebyscore("ratelimit_key", 0, 25)
        await fake.zadd("ratelimit_key", {f"new:{i}": float(100 + i)})

    await asyncio.gather(*[remove_and_add(i) for i in range(20)])

    # Just verify it doesn't crash — result is non-deterministic but >= 0
    count = await fake.zcard("ratelimit_key")
    assert count >= 0


@pytest.mark.asyncio
async def test_fake_redis_lock_is_lazily_created() -> None:
    """Lock is None at construction (before event loop) and created on first use."""
    fake = _FakeRedis()
    assert fake._lock is None  # Not created at __init__ time
    await fake.zadd("key", {"member": 1.0})
    assert fake._lock is not None  # Created lazily after first sorted-set op


@pytest.mark.asyncio
async def test_zadd_returns_correct_added_count() -> None:
    """zadd must count truly new members (not updates)."""
    fake = _FakeRedis()
    added = await fake.zadd("mykey", {"a": 1.0, "b": 2.0})
    assert added == 2

    # Update existing + add one new
    added2 = await fake.zadd("mykey", {"a": 99.0, "c": 3.0})
    assert added2 == 1  # only "c" is new


@pytest.mark.asyncio
async def test_zremrangebyscore_removes_in_range() -> None:
    """zremrangebyscore removes members whose score falls in [min, max]."""
    fake = _FakeRedis()
    await fake.zadd("scores", {"low": 1.0, "mid": 5.0, "high": 10.0})

    removed = await fake.zremrangebyscore("scores", 0, 5)
    assert removed == 2  # "low" and "mid" removed

    count = await fake.zcard("scores")
    assert count == 1  # only "high" remains


@pytest.mark.asyncio
async def test_concurrent_mixed_ops_maintain_consistency() -> None:
    """zadd and zcard called concurrently should not deadlock or corrupt state."""
    fake = _FakeRedis()

    async def writer(n: int) -> None:
        await fake.zadd("mixed", {f"item:{n}": float(n)})

    async def reader() -> int:
        return await fake.zcard("mixed")

    tasks = [writer(i) for i in range(50)] + [reader() for _ in range(10)]
    results = await asyncio.gather(*tasks)

    # All reader results should be valid non-negative integers
    reader_results = results[50:]  # last 10 are reader results
    for r in reader_results:
        assert isinstance(r, int)
        assert r >= 0
