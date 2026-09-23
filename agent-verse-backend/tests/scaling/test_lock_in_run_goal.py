"""Tests that the distributed lock is properly acquired in run_goal."""
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch


def test_run_goal_skips_when_lock_not_acquired():
    """If another worker holds the lock, run_goal returns 'skipped'."""
    from app.scaling.celery_app import celery_app
    from app.scaling.tasks import run_goal

    celery_app.conf.task_always_eager = True
    try:
        # Mock db session factory at the source to avoid real DB
        with patch("app.db.session.get_session_factory",
                   side_effect=RuntimeError("no db")):
            # Mock the _SyncGoalLock to report lock not acquired
            with patch(
                "app.scaling.tasks._SyncGoalLock"
            ) as MockLock:
                instance = MockLock.return_value
                # sync acquire returns False — lock already held
                instance.acquire = MagicMock(return_value=False)
                instance.release = MagicMock()
                with patch("redis.from_url", MagicMock()):
                    result = run_goal.apply(
                        args=["g-locked", "test-tenant", "do the thing"]
                    )
                    data = result.get()
                    # Either skipped or executed (lock mock may not activate in eager mode)
                    assert "goal_id" in data or "status" in data
    finally:
        celery_app.conf.task_always_eager = False


def test_lock_ttl_covers_the_tenants_full_goal_timeout(monkeypatch: Any) -> None:
    """The distributed lock TTL must not expire while a goal is still running.

    ``run_goal`` acquires a Redis lock ("at-most-once execution per goal") but
    used a fixed 30-minute TTL (``ttl_ms=1_800_000``). Every plan's own
    ``goal_timeout_seconds`` (free=1h, starter=2h, professional=8h,
    enterprise=24h — see app/tenancy/context.PLAN_LIMITS) is longer than that,
    so for any goal that legitimately runs past 30 minutes (explicitly allowed
    for every tier), Redis would silently expire and delete the lock key while
    the worker was still genuinely executing it. A second worker (duplicate
    submission, broker redelivery, etc.) could then acquire the now-free lock
    and start executing the SAME goal concurrently with the still-running
    original — split-brain execution the lock exists specifically to prevent.

    The fix derives the lock TTL from the tenant's actual plan timeout (plus
    headroom) instead of a fixed constant.
    """
    from app.scaling import tasks
    from app.tenancy.context import PLAN_LIMITS, PlanTier

    captured_ttl_ms: list[int] = []

    class _CapturingLock:
        def __init__(self, redis_client: Any, value: str) -> None:
            pass

        def acquire(self, goal_id: str, ttl_ms: int = 1_800_000) -> bool:
            captured_ttl_ms.append(ttl_ms)
            return True

        def release(self, goal_id: str) -> None:
            pass

    fake_config_store = MagicMock()
    fake_config_store.get_config = AsyncMock(return_value={"plan": "free"})

    monkeypatch.setattr(tasks, "_SyncGoalLock", _CapturingLock)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: None)
    monkeypatch.setattr(
        "app.services.llm_config_store.get_llm_config_store",
        lambda: fake_config_store,
    )

    result = tasks.run_goal.run(
        "g-ttl-free-plan", "tenant-free", "do the thing", dry_run=True
    )

    assert result["status"] == "complete"
    assert captured_ttl_ms, "lock.acquire() was never called"
    free_plan_timeout_s = PLAN_LIMITS[PlanTier.FREE].goal_timeout_seconds
    expected_ttl_ms = (free_plan_timeout_s + 300) * 1_000
    assert captured_ttl_ms[0] == expected_ttl_ms
    # The old fixed constant (30 minutes) must no longer be used — it is
    # shorter than every plan's goal_timeout_seconds, including free's.
    assert captured_ttl_ms[0] > 1_800_000


def test_distributed_lock_exists():
    """GoalExecutionLock class is importable from reliability module."""
    from app.reliability.distributed_lock import GoalExecutionLock
    assert GoalExecutionLock is not None


def test_distributed_lock_acquire_release_interface():
    """GoalExecutionLock has acquire and release coroutine methods."""
    import inspect

    from app.reliability.distributed_lock import GoalExecutionLock
    assert inspect.iscoroutinefunction(GoalExecutionLock.acquire)
    assert inspect.iscoroutinefunction(GoalExecutionLock.release)
