"""Beat task overlap guards using Redis SET NX with an owner token.

Wrap sensitive Celery beat tasks with a distributed lock so they
never run concurrently across replicas or if the previous run hasn't finished.

Two bugs fixed here:

* the lock was released with an unconditional ``DEL``: a run that outlived the
  TTL deleted the lock a *newer* run had since acquired, letting a third run
  overlap it. Release is now a compare-and-delete on a per-run owner token.
* the task body ran inside the same ``try`` whose ``except`` "runs anyway on
  guard failure" — so an exception raised BY THE TASK re-ran the task a second
  time. Only a failure to reach Redis falls back to running unguarded now; a
  task exception propagates normally.
"""

from __future__ import annotations

import contextlib
import functools
import uuid
from collections.abc import Callable
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Delete the key only if it still holds this run's token.
_RELEASE_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


def beat_task_guard(lock_ttl_seconds: int = 300):
    """Decorator that prevents a beat task from overlapping with another instance.

    Usage:
        @app.task
        @beat_task_guard(lock_ttl_seconds=120)
        def fire_due_schedules():
            ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            from app.scaling.celery_app import celery_app

            redis_url = celery_app.conf.broker_url or ""
            lock_key = f"beat_guard:{func.__name__}"

            if not redis_url:
                return func(*args, **kwargs)

            token = uuid.uuid4().hex
            try:
                import redis as _redis

                r = _redis.from_url(redis_url, decode_responses=True)
                acquired = r.set(lock_key, token, ex=lock_ttl_seconds, nx=True)
            except Exception as exc:
                # Redis unreachable: run unguarded rather than never running.
                logger.warning("beat_guard_error", task=func.__name__, error=str(exc)[:80])
                return func(*args, **kwargs)

            if not acquired:
                logger.info("beat_task_skipped_overlap", task=func.__name__)
                return {"skipped": True, "reason": "overlap"}
            try:
                return func(*args, **kwargs)
            finally:
                with contextlib.suppress(Exception):
                    # Redis EVAL of the constant Lua script above (not Python eval).
                    r.eval(_RELEASE_SCRIPT, 1, lock_key, token)

        return wrapper

    return decorator
