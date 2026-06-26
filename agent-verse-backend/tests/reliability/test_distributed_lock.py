import pytest
from unittest.mock import AsyncMock, MagicMock
from app.reliability.distributed_lock import GoalExecutionLock


@pytest.mark.asyncio
async def test_acquire_returns_true_when_key_new():
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(return_value=True)
    lock = GoalExecutionLock(mock_redis)
    assert await lock.acquire("goal-1") is True
    mock_redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_acquire_returns_false_when_already_locked():
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(return_value=None)  # SET NX returns None when key exists
    lock = GoalExecutionLock(mock_redis)
    assert await lock.acquire("goal-1") is False


@pytest.mark.asyncio
async def test_release_calls_lua_script():
    mock_redis = MagicMock()
    mock_redis.eval = AsyncMock(return_value=1)
    lock = GoalExecutionLock(mock_redis)
    await lock.release("goal-1")
    mock_redis.eval.assert_called_once()


@pytest.mark.asyncio
async def test_release_does_not_raise_on_error():
    mock_redis = MagicMock()
    mock_redis.eval = AsyncMock(side_effect=Exception("Redis error"))
    lock = GoalExecutionLock(mock_redis)
    await lock.release("goal-1")  # Should not raise


@pytest.mark.asyncio
async def test_extend_returns_true_when_owned():
    mock_redis = MagicMock()
    mock_redis.eval = AsyncMock(return_value=1)
    lock = GoalExecutionLock(mock_redis)
    assert await lock.extend("goal-1") is True


@pytest.mark.asyncio
async def test_extend_returns_false_when_not_owned():
    mock_redis = MagicMock()
    mock_redis.eval = AsyncMock(return_value=0)
    lock = GoalExecutionLock(mock_redis)
    assert await lock.extend("goal-1") is False


@pytest.mark.asyncio
async def test_is_locked_returns_true_when_key_exists():
    mock_redis = MagicMock()
    mock_redis.exists = AsyncMock(return_value=1)
    lock = GoalExecutionLock(mock_redis)
    assert await lock.is_locked("goal-1") is True
