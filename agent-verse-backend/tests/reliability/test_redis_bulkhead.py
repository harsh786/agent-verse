"""Tests for Redis-backed distributed bulkhead (RedisBulkhead + RedisBulkheadRegistry)."""
import asyncio
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_redis_bulkhead_acquire_success():
    """Acquiring below limit returns True."""
    from app.reliability.bulkhead import RedisBulkhead

    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=1)  # current=1, under limit=5

    bh = RedisBulkhead("tenant-1", max_concurrent=5, redis=mock_redis)
    result = await bh.acquire()
    assert result is True


@pytest.mark.asyncio
async def test_redis_bulkhead_acquire_at_limit_returns_false():
    """Acquiring at limit returns False."""
    from app.reliability.bulkhead import RedisBulkhead

    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=-1)  # at limit

    bh = RedisBulkhead("tenant-1", max_concurrent=5, redis=mock_redis)
    result = await bh.acquire()
    assert result is False


@pytest.mark.asyncio
async def test_redis_bulkhead_release_calls_lua():
    """release() calls the DECR Lua script."""
    from app.reliability.bulkhead import RedisBulkhead

    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=0)

    bh = RedisBulkhead("tenant-1", max_concurrent=5, redis=mock_redis)
    await bh.release()
    mock_redis.eval.assert_called_once()


@pytest.mark.asyncio
async def test_redis_bulkhead_context_manager_releases_on_success():
    """Context manager releases slot after successful block."""
    from app.reliability.bulkhead import RedisBulkhead

    mock_redis = AsyncMock()
    acquire_calls = []
    release_calls = []

    async def mock_eval(script, num_keys, key, *args):
        if "INCR" in script:
            acquire_calls.append(1)
            return 1
        else:
            release_calls.append(1)
            return 0

    mock_redis.eval = mock_eval
    bh = RedisBulkhead("tenant-1", max_concurrent=5, redis=mock_redis)

    async with bh:
        pass

    assert len(acquire_calls) == 1
    assert len(release_calls) == 1


@pytest.mark.asyncio
async def test_redis_bulkhead_context_manager_releases_on_exception():
    """Context manager releases slot even when exception raised inside block."""
    from app.reliability.bulkhead import RedisBulkhead

    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=1)

    bh = RedisBulkhead("tenant-1", max_concurrent=5, redis=mock_redis)

    with pytest.raises(ValueError):
        async with bh:
            raise ValueError("test error")

    # eval called twice: acquire + release
    assert mock_redis.eval.call_count == 2


@pytest.mark.asyncio
async def test_redis_bulkhead_fail_open_when_redis_unavailable():
    """When Redis is unavailable, acquire() returns True (fail-open)."""
    from app.reliability.bulkhead import RedisBulkhead

    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(side_effect=ConnectionError("Redis down"))

    bh = RedisBulkhead("tenant-1", max_concurrent=5, redis=mock_redis)
    result = await bh.acquire()
    assert result is True, "Must fail-open when Redis unavailable"


@pytest.mark.asyncio
async def test_redis_bulkhead_registry_uses_redis_when_available():
    """RedisBulkheadRegistry.get_bulkhead() returns RedisBulkhead when Redis set."""
    from app.reliability.bulkhead import RedisBulkheadRegistry, RedisBulkhead

    mock_redis = AsyncMock()
    registry = RedisBulkheadRegistry(redis=mock_redis, default_max_concurrent=10)
    bh = registry.get_bulkhead("tenant-x")
    assert isinstance(bh, RedisBulkhead)


@pytest.mark.asyncio
async def test_redis_bulkhead_registry_falls_back_to_semaphore():
    """RedisBulkheadRegistry without Redis falls back to asyncio.Semaphore."""
    from app.reliability.bulkhead import RedisBulkheadRegistry

    registry = RedisBulkheadRegistry(redis=None, default_max_concurrent=10)
    bh = registry.get_bulkhead("tenant-x")
    assert isinstance(bh, asyncio.Semaphore)


def test_agentgraph_accepts_bulkhead_registry():
    """AgentGraph.__init__ must accept bulkhead_registry parameter."""
    import inspect
    from app.agent.graph import AgentGraph

    sig = inspect.signature(AgentGraph.__init__)
    assert "bulkhead_registry" in sig.parameters, \
        "AgentGraph.__init__ must accept bulkhead_registry parameter"
