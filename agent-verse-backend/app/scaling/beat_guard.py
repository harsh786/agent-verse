"""Beat task overlap guards using Redis SET NX with an owner token.

Wrap sensitive Celery beat tasks with a distributed lock so they
never run concurrently across replicas or if the previous run hasn't finished.

Two bugs fixed here:

* the lock was released with an unconditional ``DEL``: a run that outlived the
  TTL deleted the lock a *newer* run had since acquired, letting a third run
  overlap it. Release is now a compare-and-delete on a per-run owner token.
* the task body ran inside the same ``try`` whose ``except`` "runs anyway on
  guard failure" — so an exception raised BY THE TASK re-ran the task a second
  time. A task exception now propagates normally; a failure to reach Redis
  skips the run (fail closed) instead of running it unguarded.
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


def _guard_client(broker_url: str, conf: Any) -> Any:
    """A sync Redis client for the lock, understanding Celery's Sentinel URL
    (``sentinel://[:pw@]h1:p1;h2:p2/db``) via ``Sentinel(...).master_for``."""
    import redis as _redis

    if not broker_url.startswith("sentinel://"):
        return _redis.from_url(broker_url, decode_responses=True)

    from urllib.parse import unquote

    from redis.sentinel import Sentinel

    rest = broker_url[len("sentinel://") :]
    password: str | None = None
    if "@" in rest:
        auth, rest = rest.rsplit("@", 1)
        password = unquote(auth.split(":", 1)[-1]) or None
    nodes_part, _, db_part = rest.partition("/")
    nodes: list[tuple[str, int]] = []
    for node in filter(None, (n.strip() for n in nodes_part.split(";"))):
        host, _, port = node.rpartition(":")
        nodes.append((host or node, int(port) if port.isdigit() else 26379))
    options = dict(getattr(conf, "broker_transport_options", None) or {})
    master = str(options.get("master_name") or "mymaster")
    sentinel = Sentinel(
        nodes,
        sentinel_kwargs=dict(options.get("sentinel_kwargs") or {}),
        password=password,
    )
    db_text = db_part.split("?", 1)[0]
    db = int(db_text) if db_text.isdigit() else 0
    return sentinel.master_for(master, db=db, decode_responses=True)


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
                r = _guard_client(redis_url, celery_app.conf)
                acquired = r.set(lock_key, token, ex=lock_ttl_seconds, nx=True)
            except Exception as exc:
                # Fail closed: without the lock this run could overlap another
                # replica's; the next beat tick retries. (It used to run
                # unguarded - and on Sentinel, whose sentinel:// URL
                # redis.from_url cannot parse, EVERY guarded task ran unguarded.)
                logger.warning(
                    "beat_guard_unavailable", task=func.__name__, error=str(exc)[:120]
                )
                return {"skipped": True, "reason": "guard_unavailable"}

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
