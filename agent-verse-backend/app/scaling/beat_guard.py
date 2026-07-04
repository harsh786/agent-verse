"""Beat task overlap guards using Redis SETNX.

Wrap sensitive Celery beat tasks with a distributed lock so they
never run concurrently across replicas or if the previous run hasn't finished.
"""
from __future__ import annotations

import contextlib
import functools
from collections.abc import Callable
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


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

            try:
                import redis as _redis
                r = _redis.from_url(redis_url, decode_responses=True)
                acquired = r.set(lock_key, "1", ex=lock_ttl_seconds, nx=True)
                if not acquired:
                    logger.info("beat_task_skipped_overlap", task=func.__name__)
                    return {"skipped": True, "reason": "overlap"}
                try:
                    return func(*args, **kwargs)
                finally:
                    with contextlib.suppress(Exception):
                        r.delete(lock_key)
            except Exception as exc:
                logger.warning("beat_guard_error", task=func.__name__, error=str(exc)[:80])
                return func(*args, **kwargs)  # run anyway on guard failure
        return wrapper
    return decorator
