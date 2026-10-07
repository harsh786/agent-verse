"""run_goal fails closed when it cannot take the per-goal execution lock.

Regression: a Redis error while acquiring ``goal_lock:{goal_id}`` was logged
and the goal ran WITHOUT the lock — so a redelivered / duplicated task could
execute the same goal twice concurrently. It now retries, and after the last
retry records the goal as failed (releasing its concurrency slot) instead of
running unguarded.
"""

from __future__ import annotations

from typing import Any

import pytest


class _BrokenLock:
    def __init__(self, redis_client: Any, value: str) -> None:
        pass

    def acquire(self, goal_id: str, ttl_ms: int = 0) -> bool:
        raise ConnectionError("redis unreachable")

    def release(self, goal_id: str) -> None:
        pass


class _RetryCalledError(Exception):
    pass


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import app.agent.graph as graph_mod
    from app.scaling import tasks

    seen: dict[str, Any] = {"graph_ran": False, "decrements": 0, "statuses": []}

    class _Graph:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def run(self, **kwargs: Any) -> Any:
            seen["graph_ran"] = True
            raise AssertionError("must not run without the execution lock")

    async def _dec(tenant_id: str, redis_url: str) -> None:
        seen["decrements"] += 1

    monkeypatch.setattr(graph_mod, "AgentGraph", _Graph)
    monkeypatch.setattr(tasks, "_SyncGoalLock", _BrokenLock)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: None)
    monkeypatch.setattr(tasks, "_decrement_after_completion", _dec)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "redis://localhost:6399/0")
    monkeypatch.setattr("redis.from_url", lambda *a, **k: object())
    return seen


def test_lock_error_retries_instead_of_running_unlocked(
    worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.scaling import tasks

    def _retry(*a: Any, **k: Any) -> Any:
        raise _RetryCalledError()

    monkeypatch.setattr(tasks.run_goal, "retry", _retry)
    with pytest.raises(_RetryCalledError):
        tasks.run_goal.run("g-lock-1", "tenant-l", "goal", "normal", False)
    assert worker["graph_ran"] is False


def test_lock_error_after_last_retry_fails_the_goal(
    worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.scaling import tasks

    tasks.run_goal.push_request(retries=tasks.run_goal.max_retries)
    try:
        result = tasks.run_goal.run("g-lock-2", "tenant-l", "goal", "normal", False)
    finally:
        tasks.run_goal.pop_request()

    assert result["status"] == "failed"
    assert result["reason"] == "execution_lock_unavailable"
    assert worker["graph_ran"] is False
    assert worker["decrements"] == 1


def test_non_redis_broker_without_redis_url_fails_closed(
    worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """a08-F193-05: a non-Redis broker with REDIS_URL unset ran the goal with no
    lock and no warning (the 'eager/test' lock-less path was taken)."""
    from app.scaling import tasks

    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "amqp://guest@rabbit:5672//")
    monkeypatch.delenv("REDIS_URL", raising=False)
    tasks.run_goal.push_request(retries=tasks.run_goal.max_retries)
    try:
        result = tasks.run_goal.run("g-lock-3", "tenant-l", "goal", "normal", False)
    finally:
        tasks.run_goal.pop_request()

    assert result["status"] == "failed"
    assert result["reason"] == "execution_lock_unavailable"
    assert worker["graph_ran"] is False


def test_no_broker_inline_run_says_it_skips_the_lock(
    worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.scaling import tasks

    warnings: list[str] = []
    real_warning = tasks.logger.warning

    def _warning(msg: str, *args: Any, **kwargs: Any) -> Any:
        warnings.append(str(msg))
        return real_warning(msg, *args, **kwargs)

    monkeypatch.setattr(tasks.logger, "warning", _warning)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")
    monkeypatch.setattr(
        "app.guardrails_v2.worker_binding.bind_worker_guardrail_rules", lambda: None
    )
    # The stub graph refuses to run (the run fails); only the lock decision matters.
    tasks.run_goal.run("g-lock-4", "tenant-l", "goal", "normal", False)
    assert any(w.startswith("goal_execution_lock_skipped") for w in warnings)
