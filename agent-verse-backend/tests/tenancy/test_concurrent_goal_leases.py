"""RATE-04 / RATE-05 / RATE-06: concurrent-goal slots are per-goal leases.

The slot used to be one INCR/DECR counter per tenant with ``EXPIRE 3600``
refreshed on every INCR:

* RATE-04: a goal running longer than an hour with no new submission let the
  key expire (the limit was bypassed), while a busy tenant's leaked slots never
  decayed (locked out with 429s).
* RATE-05: the release was GET-then-DECR (racy) and swallowed every error.
* RATE-06: cancelling a worker-run goal released twice (API ``goal_cancelled``
  handler + the worker's GoalCancelledError path), freeing ANOTHER goal's slot.

Slots are now members of a sorted set ``concurrent_goal_leases:{tenant}``
(member = goal_id, score = lease expiry). Acquire is idempotent per goal,
release is ``ZREM goal_id`` (a second release is a no-op), and expired leases
are reclaimed on the next acquire. These unit tests run on fakeredis (no Lua →
the conservative add-then-check path); tests/tenancy/
test_concurrent_goal_leases_redis.py covers the atomic Lua path on real Redis.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest

from app.tenancy.context import PLAN_LIMITS, PlanTier, TenantContext
from app.tenancy.limits import (
    ConcurrencyLimitUnavailableError,
    PlanLimitExceededError,
    check_and_increment_concurrent_goals,
    concurrent_goal_lease_key,
    decrement_concurrent_goals,
    renew_concurrent_goal_lease,
)

FREE = TenantContext(tenant_id="t-free", plan=PlanTier.FREE, api_key_id="k")
KEY = concurrent_goal_lease_key("t-free")


@pytest.fixture
def redis() -> Any:
    return fakeredis.FakeAsyncRedis(decode_responses=True)


async def test_limit_counts_distinct_goals(redis: Any) -> None:
    await check_and_increment_concurrent_goals(FREE, redis, goal_id="g1")
    await check_and_increment_concurrent_goals(FREE, redis, goal_id="g2")
    with pytest.raises(PlanLimitExceededError):
        await check_and_increment_concurrent_goals(FREE, redis, goal_id="g3")
    assert sorted(await redis.zrange(KEY, 0, -1)) == ["g1", "g2"]


async def test_acquire_is_idempotent_per_goal(redis: Any) -> None:
    await check_and_increment_concurrent_goals(FREE, redis, goal_id="g1")
    await check_and_increment_concurrent_goals(FREE, redis, goal_id="g1")  # redelivery
    await check_and_increment_concurrent_goals(FREE, redis, goal_id="g2")
    assert await redis.zcard(KEY) == 2


async def test_goal_running_longer_than_an_hour_is_still_counted(redis: Any) -> None:
    """RATE-04: the lease covers the plan's whole goal timeout, not a fixed hour."""
    await check_and_increment_concurrent_goals(FREE, redis, goal_id="g1")
    expiry_ms = await redis.zscore(KEY, "g1")
    timeout_s = PLAN_LIMITS[PlanTier.FREE].goal_timeout_seconds
    assert expiry_ms > (time.time() + timeout_s) * 1000
    assert await redis.pttl(KEY) > timeout_s * 1000


async def test_leaked_lease_is_reclaimed(redis: Any) -> None:
    """RATE-04: a slot whose holder died without releasing decays at lease expiry."""
    past = (time.time() - 5) * 1000
    await redis.zadd(KEY, {"dead-1": past, "dead-2": past})
    await check_and_increment_concurrent_goals(FREE, redis, goal_id="g1")
    assert await redis.zrange(KEY, 0, -1) == ["g1"]


async def test_double_release_never_frees_another_goals_slot(redis: Any) -> None:
    """RATE-05/06: cancel released the slot twice (API handler + worker)."""
    await check_and_increment_concurrent_goals(FREE, redis, goal_id="cancelled")
    await check_and_increment_concurrent_goals(FREE, redis, goal_id="running")
    assert await decrement_concurrent_goals(FREE.tenant_id, redis, goal_id="cancelled") is True
    assert await decrement_concurrent_goals(FREE.tenant_id, redis, goal_id="cancelled") is False
    assert await redis.zrange(KEY, 0, -1) == ["running"]
    await check_and_increment_concurrent_goals(FREE, redis, goal_id="new")
    with pytest.raises(PlanLimitExceededError):
        await check_and_increment_concurrent_goals(FREE, redis, goal_id="third")


async def test_release_of_an_unknown_goal_is_a_noop(redis: Any) -> None:
    await check_and_increment_concurrent_goals(FREE, redis, goal_id="g1")
    assert await decrement_concurrent_goals(FREE.tenant_id, redis, goal_id="never") is False
    assert await redis.zcard(KEY) == 1


async def test_release_failure_is_logged_not_swallowed_silently(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RATE-05: errors used to be ``except Exception: pass``."""
    import app.tenancy.limits as limits

    warned: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        limits.logger, "warning", lambda event, **kw: warned.append((event, kw))
    )
    broken = MagicMock()
    broken.zrem = AsyncMock(side_effect=ConnectionError("down"))
    assert await decrement_concurrent_goals("t1", broken, goal_id="g1") is False
    assert warned and warned[0][0] == "concurrent_goal_release_failed"
    assert warned[0][1]["goal_id"] == "g1"


async def test_renew_extends_only_a_held_lease(redis: Any) -> None:
    await check_and_increment_concurrent_goals(FREE, redis, goal_id="g1")
    before = await redis.zscore(KEY, "g1")
    assert await renew_concurrent_goal_lease("t-free", redis, goal_id="g1", lease_seconds=10**6)
    assert await redis.zscore(KEY, "g1") > before
    assert not await renew_concurrent_goal_lease(
        "t-free", redis, goal_id="gone", lease_seconds=60
    )
    assert await redis.zscore(KEY, "gone") is None


async def test_unreachable_redis_refuses(redis: Any) -> None:
    down = MagicMock()
    for op in ("eval", "zremrangebyscore", "zadd", "zcard", "zscore", "zrem", "pexpireat"):
        setattr(down, op, AsyncMock(side_effect=ConnectionError("down")))
    with pytest.raises(ConcurrencyLimitUnavailableError):
        await check_and_increment_concurrent_goals(FREE, down, goal_id="g1")


async def test_goal_id_is_required() -> None:
    with pytest.raises(TypeError):
        await check_and_increment_concurrent_goals(FREE, None)  # type: ignore[call-arg]


def _shared(redis: Any) -> Any:
    class _Shared:
        def __getattr__(self, name: str) -> Any:
            return getattr(redis, name)

        async def aclose(self) -> None:
            return None

    return _Shared()


async def test_cancel_of_a_worker_run_goal_releases_only_its_own_lease(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RATE-06 end to end: API cancel handler + worker exit both release."""
    from app.scaling import tasks

    monkeypatch.setattr("redis.asyncio.from_url", lambda *a, **k: _shared(redis))
    await check_and_increment_concurrent_goals(FREE, redis, goal_id="cancelled")
    await check_and_increment_concurrent_goals(FREE, redis, goal_id="running")
    await decrement_concurrent_goals("t-free", redis, goal_id="cancelled")  # API handler
    token = tasks._RUN_GOAL_ID.set("cancelled")  # run_goal sets it per invocation
    try:
        await tasks._decrement_after_completion("t-free", "redis://unused/0")  # worker exit
    finally:
        tasks._RUN_GOAL_ID.reset(token)
    assert await redis.zrange(KEY, 0, -1) == ["running"]


async def test_worker_lease_is_renewed_to_the_run_window_at_start(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.scaling import tasks

    monkeypatch.setattr("redis.asyncio.from_url", lambda *a, **k: _shared(redis))
    await check_and_increment_concurrent_goals(FREE, redis, goal_id="g1", lease_seconds=5)
    await tasks._renew_slot_lease("t-free", "g1", PlanTier.FREE, "redis://unused/0")
    timeout_s = PLAN_LIMITS[PlanTier.FREE].goal_timeout_seconds
    assert await redis.zscore(KEY, "g1") > (time.time() + timeout_s) * 1000
    # A goal that no longer holds a lease (finished, redelivered) is not re-admitted.
    await tasks._renew_slot_lease("t-free", "gone", PlanTier.FREE, "redis://unused/0")
    assert await redis.zscore(KEY, "gone") is None
