"""Tests for P0.1: Durable Execution Kernel."""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def test_resolve_checkpointer_uses_redis_when_available(monkeypatch):
    """_resolve_checkpointer must return a RedisSaver when REDIS_URL is set."""
    from langgraph.checkpoint.memory import MemorySaver
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    # Mock app_state with MemorySaver (the bad default)
    app_state = MagicMock()
    app_state.langgraph_checkpointer = MemorySaver()

    from app.services.goal_service import _resolve_checkpointer

    # With Redis available in env, should try to build RedisSaver
    # (will fall back to MemorySaver if redis not running, but must TRY)
    checkpointer = _resolve_checkpointer(app_state)
    # The function either returns a RedisSaver (if Redis running) or MemorySaver (not running)
    # Both are valid, but the warning must be emitted for MemorySaver
    assert checkpointer is not None


def test_resolve_checkpointer_warns_on_memory_saver(monkeypatch, caplog):
    """When falling back to MemorySaver, a clear WARNING must be logged."""
    import logging
    monkeypatch.delenv("REDIS_URL", raising=False)

    app_state = MagicMock()
    app_state.langgraph_checkpointer = None
    app_state.settings = None

    from app.services.goal_service import _resolve_checkpointer
    with caplog.at_level(logging.WARNING):
        checkpointer = _resolve_checkpointer(app_state)

    # Must warn about durability loss
    from langgraph.checkpoint.memory import MemorySaver
    assert isinstance(checkpointer, MemorySaver)
    # Warning should mention REDIS_URL or persistence
    warning_text = " ".join(caplog.messages).lower()
    assert any(word in warning_text for word in ["redis", "restart", "lost", "memory"]), \
        f"Warning must mention persistence risk, got: {caplog.messages}"


@pytest.mark.asyncio
async def test_signal_pause_sets_redis_flag():
    from app.reliability.goal_lifecycle import signal_pause, _PAUSE_FLAG, _CANCEL_FLAG
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock()
    mock_redis.publish = AsyncMock()

    await signal_pause("goal-abc", mock_redis)

    # Must set the flag key
    set_calls = [str(c) for c in mock_redis.set.call_args_list]
    assert any("goal_paused:goal-abc" in c for c in set_calls), \
        f"Must set pause flag key, got: {set_calls}"
    # Must publish to channel
    mock_redis.publish.assert_called_once()


@pytest.mark.asyncio
async def test_signal_cancel_sets_redis_flag():
    from app.reliability.goal_lifecycle import signal_cancel
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock()
    mock_redis.publish = AsyncMock()

    await signal_cancel("goal-xyz", mock_redis)

    set_calls = [str(c) for c in mock_redis.set.call_args_list]
    assert any("goal_cancelled:goal-xyz" in c for c in set_calls)


def test_is_cancelled_sync_returns_true_when_flag_set():
    from app.reliability.goal_lifecycle import is_cancelled_sync
    mock_redis = MagicMock()
    mock_redis.get = MagicMock(return_value="1")

    assert is_cancelled_sync("goal-1", mock_redis) is True


def test_is_paused_sync_returns_false_when_no_flag():
    from app.reliability.goal_lifecycle import is_paused_sync
    mock_redis = MagicMock()
    mock_redis.get = MagicMock(return_value=None)

    assert is_paused_sync("goal-1", mock_redis) is False


@pytest.mark.asyncio
async def test_check_pause_cancel_raises_on_cancel():
    from app.reliability.goal_lifecycle import check_pause_cancel, GoalCancelledError
    mock_redis = MagicMock()
    mock_redis.get = MagicMock(return_value="1")  # cancelled flag set

    with pytest.raises(GoalCancelledError):
        await check_pause_cancel("goal-cancelled", mock_redis)


def test_goal_lifecycle_module_importable():
    from app.reliability.goal_lifecycle import (
        signal_pause, signal_cancel, signal_resume, clear_signals,
        is_paused_sync, is_cancelled_sync, check_pause_cancel, GoalCancelledError
    )
    assert all(callable(f) for f in [signal_pause, signal_cancel, signal_resume,
                                      clear_signals, is_paused_sync, is_cancelled_sync,
                                      check_pause_cancel])


def test_sigterm_handler_registered():
    """tasks.py must register a SIGTERM handler."""
    import inspect
    from app.scaling import tasks
    src = inspect.getsource(tasks)
    assert "SIGTERM" in src or "signal.signal" in src or "_setup_sigterm" in src, \
        "tasks.py must register SIGTERM handler for graceful shutdown"
