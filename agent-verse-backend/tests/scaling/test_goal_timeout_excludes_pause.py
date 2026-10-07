"""a08-F193-04 / a08-F193-01: a paused goal is not timed out, and its lock lives on.

run_goal wrapped the whole run — including time paused at the step gate — in
``wait_for(timeout=goal_timeout_s)``, so a goal paused longer than its plan
timeout was failed as "Goal timed out". The goal timeout now counts active
time only (the pause gates mark their waits on an ActiveTimeBudget), and the
per-goal lock is renewed while held (its fixed TTL no longer has to outlast
the run).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import fakeredis
import pytest

from app.reliability.active_budget import ActiveTimeBudget, run_within_active_budget


async def test_paused_time_does_not_count() -> None:
    budget = ActiveTimeBudget(0.3)

    async def _work() -> str:
        with budget.paused():
            await asyncio.sleep(0.5)  # longer than the whole budget
        await asyncio.sleep(0.05)
        return "done"

    assert await run_within_active_budget(_work(), budget) == "done"
    assert budget.paused_seconds() >= 0.5


async def test_active_time_still_times_out() -> None:
    budget = ActiveTimeBudget(0.2)
    cancelled = asyncio.Event()

    async def _work() -> None:
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await run_within_active_budget(_work(), budget)
    assert time.monotonic() - started < 2
    assert cancelled.is_set()


async def test_rate_limit_budget_follows_the_active_budget() -> None:
    from app.providers.rate_limit import remaining_budget, run_with_llm_budget

    budget = ActiveTimeBudget(0.2)

    async def _probe() -> float | None:
        with budget.paused():
            await asyncio.sleep(0.3)
        return remaining_budget()

    left = await run_with_llm_budget(_probe(), budget)
    assert left is not None and left > 0.1  # the pause did not eat the budget


class _GatedRunner:
    """A runner that passes one step-boundary gate (like AgentGraph)."""

    def __init__(self) -> None:
        self._pause_gate: Any = None

    async def run(self, **_kw: Any) -> str:
        await self._pause_gate()
        return "finished"


@pytest.mark.parametrize("timeout_s", [0.6])
async def test_worker_goal_paused_longer_than_its_timeout_completes(
    monkeypatch: pytest.MonkeyPatch, timeout_s: float
) -> None:
    from app.scaling import tasks

    sync_r = fakeredis.FakeRedis(decode_responses=True)
    sync_r.set("goal_paused:g-pause", "1")
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: sync_r)
    monkeypatch.setattr(tasks, "_WORKER_SIGNAL_POLL_SECONDS", 0.05)

    async def _operator_resumes_later() -> None:
        await asyncio.sleep(timeout_s * 2)  # paused for twice the goal timeout
        sync_r.delete("goal_paused:g-pause")

    resume = asyncio.create_task(_operator_resumes_later())
    budget = ActiveTimeBudget(timeout_s)
    out = await run_within_active_budget(
        tasks._run_with_signals(
            _GatedRunner(), "goal", None, None, "g-pause", budget=budget
        ),
        budget,
    )
    await resume
    assert out == "finished"
    assert budget.paused_seconds() >= timeout_s * 1.5


class _LockRedis:
    """Sync stand-in for the lock's SET NX PX / compare-and-PEXPIRE / compare-and-DEL."""

    def __init__(self) -> None:
        self.value: str | None = None
        self.expires_at = 0.0
        self.extends = 0

    def _alive(self) -> bool:
        if self.value is not None and time.monotonic() >= self.expires_at:
            self.value = None
        return self.value is not None

    def set(self, key: str, value: str, px: int, nx: bool) -> bool:
        if nx and self._alive():
            return False
        self.value, self.expires_at = value, time.monotonic() + px / 1000
        return True

    def eval(self, script: str, _n: int, key: str, token: str, *args: str) -> int:
        if not self._alive() or self.value != token:
            return 0
        if "PEXPIRE" in script:
            self.expires_at = time.monotonic() + int(args[0]) / 1000
            self.extends += 1
            return 1
        self.value = None
        return 1


def test_goal_lock_is_renewed_while_held_and_stops_on_release() -> None:
    from app.scaling.tasks import _SyncGoalLock

    redis = _LockRedis()
    lock = _SyncGoalLock(redis, "tok-1")
    assert lock.acquire("g-1", ttl_ms=300)
    time.sleep(1.0)  # > 3 TTLs
    assert redis._alive() and redis.value == "tok-1"
    assert redis.extends >= 2
    assert _SyncGoalLock(redis, "tok-2").acquire("g-1", ttl_ms=300, renew=False) is False
    lock.release("g-1")
    assert redis.value is None
    assert lock._renew_thread is None


def test_a_lost_lock_stops_renewing() -> None:
    from app.scaling.tasks import _SyncGoalLock

    redis = _LockRedis()
    lock = _SyncGoalLock(redis, "tok-1")
    assert lock.acquire("g-2", ttl_ms=300)
    redis.value = "reaper-took-it"  # the reaper released it and another run holds it
    time.sleep(0.4)
    thread = lock._renew_thread
    assert thread is not None
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert redis.value == "reaper-took-it"  # never extended someone else's lock
    lock.release("g-2")


@pytest.mark.integration
def test_goal_lock_renewal_on_a_real_redis(redis_url: str) -> None:
    import redis as sync_redis

    from app.scaling.tasks import _SyncGoalLock

    client = sync_redis.from_url(redis_url, decode_responses=True)
    try:
        client.delete("goal_lock:g-real")
        lock = _SyncGoalLock(client, "tok-real")
        assert lock.acquire("g-real", ttl_ms=400)
        time.sleep(1.2)
        assert client.get("goal_lock:g-real") == "tok-real"
        assert not _SyncGoalLock(client, "tok-other").acquire("g-real", renew=False)
        lock.release("g-real")
        assert client.get("goal_lock:g-real") is None
    finally:
        client.close()
