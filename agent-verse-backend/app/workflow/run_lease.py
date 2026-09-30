"""Execution lease for a workflow run (WF-22).

A worker executing a run holds ``wfrun_lease:<run_id>`` in Redis and renews it
from a background thread while it works. That gives two guarantees:

* two workers never execute the same run at once (a redelivered or re-swept
  task finds the lease held and backs off);
* the stuck-run sweep can tell a dead worker (no live lease) from a merely long
  step (lease still renewed), so it re-dispatches only abandoned runs — instead
  of waiting for the broker's 25-hour visibility timeout, which is sized for the
  longest goal and applies to every task.

Synchronous Redis on purpose: Celery tasks create a fresh event loop per
``_run_async`` call, and a thread-based renewer must not share an asyncio client.
"""

from __future__ import annotations

import contextlib
import os
import threading
import uuid
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

KEY_PREFIX = "wfrun_lease:"
DEFAULT_TTL_SECONDS = 120

# Compare-and-set helpers: only the owner may renew or release.
_RENEW = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
)
_RELEASE = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


def lease_redis_url(broker_url: str) -> str:
    """Redis to hold leases in: the broker when it is Redis, else REDIS_URL;
    empty in eager/test mode (no broker), where there is nothing to coordinate."""
    if broker_url.startswith(("redis://", "rediss://", "unix://", "sentinel://")):
        return broker_url
    return os.getenv("REDIS_URL", "") if broker_url else ""


def lease_client(url: str) -> Any:
    from app.scaling.beat_guard import _guard_client
    from app.scaling.celery_app import celery_app

    return _guard_client(url, celery_app.conf)


def lease_alive(redis_client: Any, run_id: str) -> bool:
    return bool(redis_client.exists(f"{KEY_PREFIX}{run_id}"))


class RunLease:
    """A renewed, owner-checked lease on one run."""

    def __init__(self, redis_client: Any, run_id: str, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        self._redis = redis_client
        self._key = f"{KEY_PREFIX}{run_id}"
        self._token = uuid.uuid4().hex
        self._ttl = ttl
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def acquire(self) -> bool:
        if not self._redis.set(self._key, self._token, ex=self._ttl, nx=True):
            return False
        self._thread = threading.Thread(target=self._renew_loop, daemon=True)
        self._thread.start()
        return True

    def _renew_loop(self) -> None:
        while not self._stop.wait(self._ttl / 3):
            try:
                if not self._redis.eval(_RENEW, 1, self._key, self._token, self._ttl):
                    _log.warning("workflow_run_lease_lost", key=self._key)
                    return
            except Exception as exc:  # keep trying until the TTL runs out
                _log.warning("workflow_run_lease_renew_failed", key=self._key, error=str(exc))

    def release(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with contextlib.suppress(Exception):
            self._redis.eval(_RELEASE, 1, self._key, self._token)
