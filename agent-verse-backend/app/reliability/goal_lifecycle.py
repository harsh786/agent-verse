"""Cross-process goal lifecycle signals via Redis pub/sub + flag keys.

Allows API server to signal Celery workers to pause, cancel, or resume
goals running in separate processes without shared memory.

Usage:
    # From API server (async):
    await signal_pause("goal-123", redis_client)
    await signal_cancel("goal-123", redis_client)
    await signal_resume("goal-123", redis_client)

    # From Celery worker (sync check before each step):
    if is_cancelled_sync("goal-123", sync_redis):
        raise GoalCancelledError("Cancelled by operator")
    await check_pause_cancel("goal-123", sync_redis)  # blocks if paused
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

_PAUSE_FLAG = "goal_paused:{goal_id}"
_CANCEL_FLAG = "goal_cancelled:{goal_id}"
_PAUSE_CHANNEL = "goal_pause:{goal_id}"
_CANCEL_CHANNEL = "goal_cancel:{goal_id}"
_FLAG_TTL = 7200  # 2 hours


class GoalCancelledError(Exception):
    """Raised when a goal is cancelled by an operator signal."""


async def signal_pause(goal_id: str, redis: Any) -> None:
    """Signal a running goal to pause. Works across process boundaries via Redis."""
    try:
        key = _PAUSE_FLAG.format(goal_id=goal_id)
        channel = _PAUSE_CHANNEL.format(goal_id=goal_id)
        await redis.set(key, "1", ex=_FLAG_TTL)
        await redis.publish(channel, "pause")
        logger.info("goal_pause_signalled", goal_id=goal_id)
    except Exception as exc:
        logger.warning("goal_pause_signal_failed", goal_id=goal_id, error=str(exc))


async def signal_resume(goal_id: str, redis: Any) -> None:
    """Signal a paused goal to resume."""
    try:
        key = _PAUSE_FLAG.format(goal_id=goal_id)
        channel = _PAUSE_CHANNEL.format(goal_id=goal_id)
        await redis.delete(key)
        await redis.publish(channel, "resume")
        logger.info("goal_resume_signalled", goal_id=goal_id)
    except Exception as exc:
        logger.warning("goal_resume_signal_failed", goal_id=goal_id, error=str(exc))


async def signal_cancel(goal_id: str, redis: Any) -> None:
    """Signal a running goal to cancel immediately."""
    try:
        key = _CANCEL_FLAG.format(goal_id=goal_id)
        channel = _CANCEL_CHANNEL.format(goal_id=goal_id)
        await redis.set(key, "1", ex=_FLAG_TTL)
        await redis.publish(channel, "cancel")
        logger.info("goal_cancel_signalled", goal_id=goal_id)
    except Exception as exc:
        logger.warning("goal_cancel_signal_failed", goal_id=goal_id, error=str(exc))


async def clear_signals(goal_id: str, redis: Any) -> None:
    """Clear all signals for a completed/failed goal."""
    try:
        await redis.delete(
            _PAUSE_FLAG.format(goal_id=goal_id),
            _CANCEL_FLAG.format(goal_id=goal_id),
        )
    except Exception:
        pass


def is_paused_sync(goal_id: str, redis_sync: Any) -> bool:
    """Synchronous check — use from Celery task context."""
    try:
        return bool(redis_sync.get(_PAUSE_FLAG.format(goal_id=goal_id)))
    except Exception:
        return False


def is_cancelled_sync(goal_id: str, redis_sync: Any) -> bool:
    """Synchronous check — use from Celery task context."""
    try:
        return bool(redis_sync.get(_CANCEL_FLAG.format(goal_id=goal_id)))
    except Exception:
        return False


async def check_pause_cancel(goal_id: str, redis_sync: Any) -> None:
    """Check pause/cancel signals. Call between each wave step.

    - If cancelled: raises GoalCancelledError immediately
    - If paused: blocks with polling until resumed or cancelled (max 4 hours)
    """
    if is_cancelled_sync(goal_id, redis_sync):
        raise GoalCancelledError(f"Goal {goal_id} cancelled by operator")

    if is_paused_sync(goal_id, redis_sync):
        logger.info("goal_paused_waiting_for_resume", goal_id=goal_id)
        max_wait = 4 * 3600  # 4 hours max pause
        waited = 0
        while is_paused_sync(goal_id, redis_sync) and waited < max_wait:
            await asyncio.sleep(5)
            waited += 5
            if is_cancelled_sync(goal_id, redis_sync):
                raise GoalCancelledError(f"Goal {goal_id} cancelled while paused")
        logger.info("goal_resumed", goal_id=goal_id, waited_seconds=waited)
