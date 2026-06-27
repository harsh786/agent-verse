"""Tests for GoalLifecycle cross-process signal bus."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.reliability.goal_lifecycle import (
    signal_pause, signal_resume, signal_cancel, clear_signals,
    is_paused_sync, is_cancelled_sync, GoalCancelledError, check_pause_cancel
)


@pytest.mark.asyncio
async def test_signal_resume_deletes_pause_flag():
    mock_redis = AsyncMock()
    mock_redis.delete = AsyncMock()
    mock_redis.publish = AsyncMock()

    await signal_resume("goal-1", mock_redis)

    delete_calls = [str(c) for c in mock_redis.delete.call_args_list]
    assert any("goal_paused:goal-1" in c for c in delete_calls)


@pytest.mark.asyncio
async def test_clear_signals_deletes_both_flags():
    mock_redis = AsyncMock()
    mock_redis.delete = AsyncMock()

    await clear_signals("goal-done", mock_redis)

    # Should delete both pause and cancel flags
    mock_redis.delete.assert_called_once()
    call_args = str(mock_redis.delete.call_args)
    assert "goal_paused" in call_args and "goal_cancelled" in call_args


@pytest.mark.asyncio
async def test_check_pause_cancel_passes_when_no_signals():
    mock_redis = MagicMock()
    mock_redis.get = MagicMock(return_value=None)

    # Should not raise when no signals
    await check_pause_cancel("goal-ok", mock_redis)


@pytest.mark.asyncio
async def test_signal_pause_noop_on_redis_error():
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(side_effect=ConnectionError("Redis down"))
    mock_redis.publish = AsyncMock(side_effect=ConnectionError("Redis down"))

    # Must not raise — Redis errors are swallowed
    await signal_pause("goal-1", mock_redis)
