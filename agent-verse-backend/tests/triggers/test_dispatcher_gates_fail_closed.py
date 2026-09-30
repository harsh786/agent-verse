"""TRG-14: trigger rate limit / bulkhead fail closed; a gate skip does not burn dedup.

During a Redis blip the rate limiter and bulkhead allowed everything (fail
open), and a fire skipped by rate limit / circuit / bulkhead had already claimed
its 60 s dedup key, so a legitimate redelivery was dropped as a duplicate.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from app.tenancy.context import PlanTier, TenantContext
from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.models import TriggerSpec, TriggerType

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")


class _Redis:
    """set NX / delete for dedup; incr can be made to fail."""

    def __init__(self, *, fail_incr: bool = False) -> None:
        self.keys: dict[str, Any] = {}
        self.fail_incr = fail_incr

    async def set(self, key: str, value: Any, ex: int = 0, nx: bool = False) -> Any:
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        return sum(1 for k in keys if self.keys.pop(k, None) is not None)

    async def incr(self, key: str) -> int:
        if self.fail_incr:
            raise ConnectionError("redis down")
        self.keys[key] = int(self.keys.get(key, 0)) + 1
        return int(self.keys[key])

    async def decr(self, key: str) -> int:
        self.keys[key] = int(self.keys.get(key, 0)) - 1
        return int(self.keys[key])

    async def expire(self, key: str, ttl: int) -> bool:
        return True


def _spec(max_per_hour: int = 1) -> TriggerSpec:
    spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK, goal_template="go",
                       max_firings_per_hour=max_per_hour)
    spec.trigger_id = "tr-gates"  # type: ignore[attr-defined]
    return spec


def _dispatcher(redis: _Redis) -> tuple[TriggerDispatcher, Any]:
    gs = SimpleNamespace(create_goal=AsyncMock(return_value={"goal_id": "g"}))
    d = TriggerDispatcher(goal_service=gs, redis=redis)
    d._write_dlq = AsyncMock()  # type: ignore[method-assign]
    return d, gs


async def test_rate_limiter_redis_error_skips_as_unavailable_and_dead_letters() -> None:
    d, gs = _dispatcher(_Redis(fail_incr=True))
    event = await d.dispatch(_spec(), {"a": 1}, CTX, message_id="m1")
    assert event.skip_reason == "rate_limit_unavailable"
    gs.create_goal.assert_not_awaited()
    d._write_dlq.assert_awaited()
    assert d._write_dlq.await_args.args[2] == "RATE_LIMIT_UNAVAILABLE"


async def test_bulkhead_redis_error_skips_as_unavailable() -> None:
    redis = _Redis()
    d, gs = _dispatcher(redis)

    async def _boom(*_: Any, **__: Any) -> bool:
        raise ConnectionError("redis down")

    d._bulkhead._redis = SimpleNamespace(incr=_boom)  # type: ignore[assignment]
    event = await d.dispatch(_spec(max_per_hour=0), {"a": 1}, CTX, message_id="m2")
    assert event.skip_reason == "bulkhead_unavailable"
    gs.create_goal.assert_not_awaited()


async def test_rate_limited_fire_can_be_redelivered_once_the_window_allows_it() -> None:
    redis = _Redis()
    d, gs = _dispatcher(redis)
    allowed = iter([False, True])

    async def _check(*_: Any, **__: Any) -> bool:
        return next(allowed)

    d._rate_limiter.check = _check  # type: ignore[method-assign]
    first = await d.dispatch(_spec(), {"a": 1}, CTX, message_id="m3")
    assert first.skip_reason == "rate_limit"
    retry = await d.dispatch(_spec(), {"a": 1}, CTX, message_id="m3")  # same delivery
    assert retry.skip_reason is None and retry.goal_created is True
    gs.create_goal.assert_awaited_once()


async def test_failed_goal_enqueue_does_not_block_the_redelivery() -> None:
    """A firing whose goal could not be enqueued has not run: neither the Redis
    claim nor the durable trigger_events row may dedup its redelivery."""
    d, gs = _dispatcher(_Redis())
    d._rate_limiter.check = AsyncMock(return_value=True)  # type: ignore[method-assign]
    gs.create_goal = AsyncMock(side_effect=[RuntimeError("queue down"), {"goal_id": "g2"}])
    persisted: list[Any] = []

    async def _persist(event: Any) -> None:
        persisted.append(event)

    d._persist_event = _persist  # type: ignore[method-assign]
    failed = await d.dispatch(_spec(max_per_hour=0), {"a": 1}, CTX, message_id="m5")
    assert failed.goal_created is False
    retry = await d.dispatch(_spec(max_per_hour=0), {"a": 1}, CTX, message_id="m5")
    assert retry.goal_created is True and retry.goal_id == "g2"
    # The failure row is audited under its own key, never the firing's real key.
    assert persisted[0].idempotency_key != retry.idempotency_key
    assert persisted[0].idempotency_key.startswith(retry.idempotency_key + ":failed:")


async def test_a_real_duplicate_is_still_deduped() -> None:
    d, gs = _dispatcher(_Redis())
    d._rate_limiter.check = AsyncMock(return_value=True)  # type: ignore[method-assign]
    await d.dispatch(_spec(max_per_hour=0), {"a": 1}, CTX, message_id="m4")
    again = await d.dispatch(_spec(max_per_hour=0), {"a": 1}, CTX, message_id="m4")
    assert again.skip_reason == "dedup"
    gs.create_goal.assert_awaited_once()
