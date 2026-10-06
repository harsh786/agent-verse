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

The step-boundary gates that block while paused are the runners' own:
``app.scaling.tasks._make_worker_pause_gate`` / ``_run_with_signals`` (worker)
and ``GoalService._make_pause_gate`` (in-process). The caller-less
``check_pause_cancel`` duplicate was removed (a08-F193-02).
"""

from __future__ import annotations

import contextlib
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

_PAUSE_FLAG = "goal_paused:{goal_id}"
_CANCEL_FLAG = "goal_cancelled:{goal_id}"
_PAUSE_CHANNEL = "goal_pause:{goal_id}"
_CANCEL_CHANNEL = "goal_cancel:{goal_id}"
_FLAG_TTL = 7200  # 2 hours
# A pause is an operator decision and must outlive a lunch break: with the
# 2-hour flag TTL a paused goal silently resumed itself once the key expired.
# The TTL only garbage-collects flags of goals that never came back.
_PAUSE_TTL = 7 * 24 * 3600


class GoalCancelledError(Exception):
    """Raised when a goal is cancelled by an operator signal."""


async def _signal(
    goal_id: str,
    redis: Any,
    *,
    what: str,
    write: Any,
    channel: str,
    message: str,
    strict: bool,
) -> None:
    """Write the durable flag (what runners poll), then publish best-effort.

    With *strict* a failed flag write raises: the caller is about to report
    the action as done and must not when no runner can observe it (fail
    closed). The pub/sub message is only a wake-up hint — runners poll the
    flag — so a publish failure is logged, never raised.
    """
    try:
        await write()
    except Exception as exc:
        logger.warning(f"goal_{what}_signal_failed", goal_id=goal_id, error=str(exc))
        if strict:
            raise
        return
    try:
        await redis.publish(channel, message)
    except Exception as exc:
        logger.warning(f"goal_{what}_publish_failed", goal_id=goal_id, error=str(exc))
    logger.info(f"goal_{what}_signalled", goal_id=goal_id)


async def signal_pause(goal_id: str, redis: Any, *, strict: bool = False) -> None:
    """Signal a running goal to pause. Works across process boundaries via Redis."""
    key = _PAUSE_FLAG.format(goal_id=goal_id)

    async def _write() -> None:
        await redis.set(key, "1", ex=_PAUSE_TTL)

    await _signal(
        goal_id, redis, what="pause", write=_write,
        channel=_PAUSE_CHANNEL.format(goal_id=goal_id), message="pause", strict=strict,
    )


async def signal_resume(goal_id: str, redis: Any, *, strict: bool = False) -> None:
    """Signal a paused goal to resume."""
    key = _PAUSE_FLAG.format(goal_id=goal_id)

    async def _write() -> None:
        await redis.delete(key)

    await _signal(
        goal_id, redis, what="resume", write=_write,
        channel=_PAUSE_CHANNEL.format(goal_id=goal_id), message="resume", strict=strict,
    )


async def signal_cancel(goal_id: str, redis: Any, *, strict: bool = False) -> None:
    """Signal a running goal to cancel immediately."""
    key = _CANCEL_FLAG.format(goal_id=goal_id)

    async def _write() -> None:
        await redis.set(key, "1", ex=_FLAG_TTL)

    await _signal(
        goal_id, redis, what="cancel", write=_write,
        channel=_CANCEL_CHANNEL.format(goal_id=goal_id), message="cancel", strict=strict,
    )


async def withdraw_cancel(goal_id: str, redis: Any) -> None:
    """Best-effort undo of :func:`signal_cancel` when the cancel could not be
    persisted (the API answers 503 and the goal must keep running)."""
    try:
        await redis.delete(_CANCEL_FLAG.format(goal_id=goal_id))
    except Exception as exc:
        logger.warning("goal_cancel_withdraw_failed", goal_id=goal_id, error=str(exc))


async def clear_signals(goal_id: str, redis: Any) -> None:
    """Clear all signals for a completed/failed goal."""
    with contextlib.suppress(Exception):
        await redis.delete(
            _PAUSE_FLAG.format(goal_id=goal_id),
            _CANCEL_FLAG.format(goal_id=goal_id),
        )


# a08-F193-03: every flag read FAILS CLOSED. A read error used to count as
# "no flag", so one Redis blip hid a cancel from the runner and, inside the
# pause wait loops, ended an operator's pause (the goal resumed and emitted
# goal_execution_resumed with no resume_goal call). An unreadable cancel flag
# now counts as cancelled and an unreadable pause flag as still paused — the
# same stance as the emergency stop (UNVERIFIABLE_REASON) on a Redis error.


def _read_failed(flag: str, goal_id: str, exc: Exception) -> bool:
    logger.warning(f"goal_{flag}_flag_read_failed", goal_id=goal_id, error=str(exc))
    return True


async def is_paused(goal_id: str, redis: Any) -> bool:
    """Async check of the cross-process pause flag (API-server / in-process runs).

    True on a read error (fail closed: the goal stays paused).
    """
    try:
        return bool(await redis.get(_PAUSE_FLAG.format(goal_id=goal_id)))
    except Exception as exc:
        return _read_failed("pause", goal_id, exc)


async def is_cancelled(goal_id: str, redis: Any) -> bool:
    """Async check of the cross-process cancel flag. True on a read error."""
    try:
        return bool(await redis.get(_CANCEL_FLAG.format(goal_id=goal_id)))
    except Exception as exc:
        return _read_failed("cancel", goal_id, exc)


def is_paused_sync(goal_id: str, redis_sync: Any) -> bool:
    """Synchronous check — use from Celery task context. True on a read error."""
    try:
        return bool(redis_sync.get(_PAUSE_FLAG.format(goal_id=goal_id)))
    except Exception as exc:
        return _read_failed("pause", goal_id, exc)


def is_cancelled_sync(goal_id: str, redis_sync: Any) -> bool:
    """Synchronous check — use from Celery task context. True on a read error."""
    try:
        return bool(redis_sync.get(_CANCEL_FLAG.format(goal_id=goal_id)))
    except Exception as exc:
        return _read_failed("cancel", goal_id, exc)
