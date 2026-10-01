"""WF-16: a goal never starts when the emergency-stop state cannot be read.

The start-of-goal check was wrapped in ``except Exception: log``, so a Redis
error let a goal start during an active stop. It now retries the task with
backoff and, after the last retry, records the goal as blocked — never runs it.
"""

from __future__ import annotations

from typing import Any

import pytest
from celery.exceptions import Retry

from app.governance.emergency_stop import UNVERIFIABLE_REASON
from tests.scaling.test_worker_runtime_profile import _run, worker  # noqa: F401


class _DownRedis:
    def get(self, key: str) -> Any:
        raise ConnectionError("redis is down")

    def smembers(self, key: str) -> Any:
        raise ConnectionError("redis is down")

    def scan_iter(self, *a: Any, **k: Any) -> Any:
        raise ConnectionError("redis is down")


def test_redis_error_retries_instead_of_running(
    worker: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.scaling import tasks

    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: _DownRedis())
    worker["with_context"]({})
    # Called directly (no worker request) Celery re-raises the cause instead of
    # scheduling the retry; either way the goal did not run.
    with pytest.raises((Retry, ConnectionError)):
        _run()
    assert worker["graphs"] == []


def test_redis_error_on_last_retry_blocks_the_goal(
    worker: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.scaling import tasks

    blocked: list[tuple[str, str, str]] = []

    async def _mark(goal_id: str, tenant_id: str, reason: str) -> bool:
        blocked.append((goal_id, tenant_id, reason))
        return True

    async def _noop(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: _DownRedis())
    monkeypatch.setattr(tasks, "_mark_goal_blocked", _mark)
    monkeypatch.setattr(tasks, "_decrement_after_completion", _noop)
    worker["with_context"]({})
    tasks.run_goal.push_request(retries=tasks.run_goal.max_retries)
    try:
        result = _run()
    finally:
        tasks.run_goal.pop_request()
    assert result == {"status": "blocked", "reason": UNVERIFIABLE_REASON}
    assert blocked == [("g-prof", "t-prof", UNVERIFIABLE_REASON)]
    assert worker["graphs"] == []


@pytest.mark.integration
def test_real_redis_stopped_mid_test_never_runs_the_goal(
    worker: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real Redis serves the stop flags, then goes away before the goal starts."""
    import redis as redis_lib

    from app.scaling import tasks
    from tests._test_backends import redis_container

    with redis_container() as url:
        client = redis_lib.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
        assert client.ping()
        monkeypatch.setattr(tasks, "_get_sync_redis", lambda: client)
    # The container is stopped now; the next read fails.
    worker["with_context"]({})
    with pytest.raises((Retry, redis_lib.exceptions.RedisError)):
        _run()
    assert worker["graphs"] == []
