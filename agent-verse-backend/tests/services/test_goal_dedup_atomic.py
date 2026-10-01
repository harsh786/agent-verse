"""SVC-01: identical concurrent goal submissions must run once.

get_existing() and register() were separate calls, register()'s False was
ignored and Redis errors were swallowed, so two concurrent identical submits
both executed. The key was released only for locally dispatched terminal
events (never by the worker), and the in-memory fallback map never expired.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.dedup import GoalDeduplicator, release_goal_claim
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-atomic", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _NxRedis:
    """Async fake honouring SET NX; every op yields so callers interleave."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.fail = False

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> Any:
        await asyncio.sleep(0)
        if self.fail:
            raise ConnectionError("redis down")
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def get(self, key: str) -> str | None:
        await asyncio.sleep(0)
        if self.fail:
            raise ConnectionError("redis down")
        return self.data.get(key)

    async def delete(self, *keys: str) -> int:
        await asyncio.sleep(0)
        return sum(1 for k in keys if self.data.pop(k, None) is not None)


async def test_concurrent_claims_have_exactly_one_winner() -> None:
    dedup = GoalDeduplicator(redis=_NxRedis())
    results = await asyncio.gather(*(dedup.claim("t", "same goal", f"g{i}") for i in range(20)))
    winners = [f"g{i}" for i, r in enumerate(results) if r is None]
    assert len(winners) == 1
    assert all(r == winners[0] for r in results if r is not None)


async def test_concurrent_identical_submits_create_one_goal() -> None:
    dedup = GoalDeduplicator(redis=_NxRedis())
    svc = GoalService(task_queue=MagicMock())
    with (
        patch("app.services.dedup._default_deduplicator", dedup),
        patch("app.tenancy.limits.check_and_increment_concurrent_goals", AsyncMock()),
    ):
        a, b = await asyncio.gather(
            svc.submit_goal(goal="Ship it", priority="normal", dry_run=False, tenant_ctx=T),
            svc.submit_goal(goal="Ship it", priority="normal", dry_run=False, tenant_ctx=T),
        )
    deduped = [r for r in (a, b) if r.get("deduplicated")]
    ran = [r for r in (a, b) if not r.get("deduplicated")]
    assert len(ran) == 1 and len(deduped) == 1
    assert deduped[0]["goal_id"] == ran[0]["goal_id"]


async def test_slot_refusal_releases_the_claim() -> None:
    """A submission refused by the concurrency limit must not leave a claim that
    dedups the retry onto a goal that never existed."""
    from app.tenancy.limits import PlanLimitExceededError

    dedup = GoalDeduplicator(redis=_NxRedis())
    svc = GoalService(task_queue=MagicMock())
    with (
        patch("app.services.dedup._default_deduplicator", dedup),
        patch(
            "app.tenancy.limits.check_and_increment_concurrent_goals",
            AsyncMock(side_effect=PlanLimitExceededError("limit")),
        ),
        pytest.raises(PlanLimitExceededError),
    ):
        await svc.submit_goal(goal="Retry me", priority="normal", dry_run=False, tenant_ctx=T)
    assert dedup._redis.data == {}


async def test_redis_error_means_no_dedup_not_a_silent_merge(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = _NxRedis()
    redis.fail = True
    dedup = GoalDeduplicator(redis=redis)
    assert await dedup.claim("t", "g", "g1") is None
    assert await dedup.claim("t", "g", "g2") is None  # nothing silently remembered
    assert dedup._mem == {}


async def test_in_memory_fallback_expires() -> None:
    dedup = GoalDeduplicator(ttl=1)
    assert await dedup.claim("t", "g", "g1") is None
    assert await dedup.claim("t", "g", "g2") == "g1"
    dedup._mem[next(iter(dedup._mem))] = ("g1", time.monotonic() - 1)
    assert await dedup.claim("t", "g", "g3") is None


async def test_release_goal_only_drops_its_own_claim() -> None:
    redis = _NxRedis()
    dedup = GoalDeduplicator(redis=redis)
    assert await dedup.claim("t", "g", "g1") is None
    await dedup.release_goal("other-goal")
    assert await dedup.claim("t", "g", "g2") == "g1"
    await dedup.release_goal("g1")
    assert await dedup.claim("t", "g", "g3") is None


async def test_worker_terminal_path_releases_the_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    import redis.asyncio as aioredis

    import app.scaling.tasks as tasks_mod

    redis = _NxRedis()
    redis.aclose = AsyncMock()  # type: ignore[attr-defined]
    assert await GoalDeduplicator(redis=redis).claim("t", "worker goal", "gw") is None
    monkeypatch.setattr(aioredis, "from_url", lambda *_a, **_k: redis)
    monkeypatch.setattr(
        "app.tenancy.limits.decrement_concurrent_goals", AsyncMock(return_value=None)
    )
    token = tasks_mod._RUN_GOAL_ID.set("gw")
    try:
        await tasks_mod._decrement_after_completion("t", "redis://unused")
    finally:
        tasks_mod._RUN_GOAL_ID.reset(token)
    assert redis.data == {}


async def test_release_goal_claim_helper_is_noop_without_claim() -> None:
    redis = _NxRedis()
    await release_goal_claim(redis, "nothing")
    assert redis.data == {}
