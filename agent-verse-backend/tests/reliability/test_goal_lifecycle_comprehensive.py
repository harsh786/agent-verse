"""Comprehensive tests for app/reliability/goal_lifecycle.py — targeting 90%+ coverage."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.reliability.goal_lifecycle import (
    _CANCEL_FLAG,
    _FLAG_TTL,
    _PAUSE_TTL,
    _PAUSE_FLAG,
    GoalCancelledError,
    clear_signals,
    is_cancelled_sync,
    is_paused_sync,
    signal_cancel,
    signal_pause,
    signal_resume,
)

# ── signal_pause ──────────────────────────────────────────────────────────────

class TestSignalPause:
    async def test_sets_pause_flag(self) -> None:
        mock_redis = AsyncMock()
        await signal_pause("goal-1", mock_redis)
        # A pause outlives the 2 h signal TTL: it used to expire and silently
        # resume the goal.
        mock_redis.set.assert_called_once_with(
            _PAUSE_FLAG.format(goal_id="goal-1"), "1", ex=_PAUSE_TTL
        )

    async def test_publishes_pause_channel(self) -> None:
        mock_redis = AsyncMock()
        await signal_pause("goal-1", mock_redis)
        mock_redis.publish.assert_called_once_with(
            "goal_pause:goal-1", "pause"
        )

    async def test_redis_error_suppressed(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(side_effect=Exception("Redis down"))
        await signal_pause("goal-1", mock_redis)  # must not raise


# ── signal_resume ─────────────────────────────────────────────────────────────

class TestSignalResume:
    async def test_deletes_pause_flag(self) -> None:
        mock_redis = AsyncMock()
        await signal_resume("goal-1", mock_redis)
        mock_redis.delete.assert_called_once_with(
            _PAUSE_FLAG.format(goal_id="goal-1")
        )

    async def test_publishes_resume_channel(self) -> None:
        mock_redis = AsyncMock()
        await signal_resume("goal-1", mock_redis)
        mock_redis.publish.assert_called_once_with(
            "goal_pause:goal-1", "resume"
        )

    async def test_redis_error_suppressed(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(side_effect=Exception("Redis down"))
        await signal_resume("goal-1", mock_redis)  # must not raise


# ── signal_cancel ─────────────────────────────────────────────────────────────

class TestSignalCancel:
    async def test_sets_cancel_flag(self) -> None:
        mock_redis = AsyncMock()
        await signal_cancel("goal-1", mock_redis)
        mock_redis.set.assert_called_once_with(
            _CANCEL_FLAG.format(goal_id="goal-1"), "1", ex=_FLAG_TTL
        )

    async def test_publishes_cancel_channel(self) -> None:
        mock_redis = AsyncMock()
        await signal_cancel("goal-1", mock_redis)
        mock_redis.publish.assert_called_once_with(
            "goal_cancel:goal-1", "cancel"
        )

    async def test_redis_error_suppressed(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(side_effect=Exception("Redis down"))
        await signal_cancel("goal-1", mock_redis)  # must not raise


# ── clear_signals ─────────────────────────────────────────────────────────────

class TestClearSignals:
    async def test_deletes_both_flags(self) -> None:
        mock_redis = AsyncMock()
        await clear_signals("goal-1", mock_redis)
        mock_redis.delete.assert_called_once()
        args = mock_redis.delete.call_args[0]
        assert _PAUSE_FLAG.format(goal_id="goal-1") in args
        assert _CANCEL_FLAG.format(goal_id="goal-1") in args

    async def test_redis_error_suppressed(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(side_effect=Exception("Redis down"))
        await clear_signals("goal-1", mock_redis)  # must not raise


# ── is_paused_sync ────────────────────────────────────────────────────────────

class TestIsPausedSync:
    def test_returns_true_when_flag_set(self) -> None:
        mock_redis = MagicMock()
        mock_redis.get = MagicMock(return_value="1")
        assert is_paused_sync("goal-1", mock_redis) is True

    def test_returns_false_when_no_flag(self) -> None:
        mock_redis = MagicMock()
        mock_redis.get = MagicMock(return_value=None)
        assert is_paused_sync("goal-1", mock_redis) is False

    def test_redis_error_fails_closed(self) -> None:
        # a08-F193-03: an unreadable pause flag keeps the goal paused.
        mock_redis = MagicMock()
        mock_redis.get = MagicMock(side_effect=Exception("Redis down"))
        assert is_paused_sync("goal-1", mock_redis) is True

    def test_calls_correct_key(self) -> None:
        mock_redis = MagicMock()
        mock_redis.get = MagicMock(return_value=None)
        is_paused_sync("goal-abc", mock_redis)
        mock_redis.get.assert_called_once_with(_PAUSE_FLAG.format(goal_id="goal-abc"))


# ── is_cancelled_sync ─────────────────────────────────────────────────────────

class TestIsCancelledSync:
    def test_returns_true_when_flag_set(self) -> None:
        mock_redis = MagicMock()
        mock_redis.get = MagicMock(return_value="1")
        assert is_cancelled_sync("goal-1", mock_redis) is True

    def test_returns_false_when_no_flag(self) -> None:
        mock_redis = MagicMock()
        mock_redis.get = MagicMock(return_value=None)
        assert is_cancelled_sync("goal-1", mock_redis) is False

    def test_redis_error_fails_closed(self) -> None:
        # a08-F193-03: an unreadable cancel flag counts as cancelled.
        mock_redis = MagicMock()
        mock_redis.get = MagicMock(side_effect=Exception("Redis down"))
        assert is_cancelled_sync("goal-1", mock_redis) is True

    def test_calls_correct_key(self) -> None:
        mock_redis = MagicMock()
        mock_redis.get = MagicMock(return_value=None)
        is_cancelled_sync("goal-xyz", mock_redis)
        mock_redis.get.assert_called_once_with(_CANCEL_FLAG.format(goal_id="goal-xyz"))


# ── GoalCancelledError ────────────────────────────────────────────────────────

class TestGoalCancelledError:
    def test_is_exception(self) -> None:
        err = GoalCancelledError("Goal g1 cancelled by operator")
        assert isinstance(err, Exception)
        assert "g1" in str(err)

    def test_can_be_raised(self) -> None:
        with pytest.raises(GoalCancelledError):
            raise GoalCancelledError("cancelled")


# ── key format constants ──────────────────────────────────────────────────────

class TestKeyFormats:
    def test_pause_flag_format(self) -> None:
        key = _PAUSE_FLAG.format(goal_id="g123")
        assert "g123" in key
        assert "pause" in key.lower()

    def test_cancel_flag_format(self) -> None:
        key = _CANCEL_FLAG.format(goal_id="g123")
        assert "g123" in key
        assert "cancel" in key.lower()

    def test_flag_ttl_is_reasonable(self) -> None:
        assert _FLAG_TTL == 7200  # 2 hours
