"""B2-1: rate limit / bulkhead run BEFORE the dedup slot is claimed.

The dispatcher claimed the 60 s Redis dedup key (SET NX) first and only then
checked the rate limit, circuit and bulkhead. A throttled firing released the
key afterwards, but a redelivery that arrived while the throttled original held
the claim was dropped as a duplicate, and then the original was dropped as
throttled: the event was lost twice over. A throttled firing also left no way to
replay it (only an audit row).

Now: a non-claiming dedup peek (so a duplicate never burns a rate token), then
the governance gates, then the atomic claim. A throttled firing never touches
the dedup key and is dead-lettered (replayable through the trigger DLQ) unless
the caller answers the sender with 429 itself.
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
    def __init__(self) -> None:
        self.keys: dict[str, Any] = {}
        self.set_calls: list[str] = []

    async def set(self, key: str, value: Any, ex: int = 0, nx: bool = False) -> Any:
        self.set_calls.append(key)
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True

    async def exists(self, *keys: str) -> int:
        return sum(1 for k in keys if k in self.keys)

    async def delete(self, *keys: str) -> int:
        return sum(1 for k in keys if self.keys.pop(k, None) is not None)

    async def incr(self, key: str) -> int:
        self.keys[key] = int(self.keys.get(key, 0)) + 1
        return int(self.keys[key])

    async def decr(self, key: str) -> int:
        self.keys[key] = int(self.keys.get(key, 0)) - 1
        return int(self.keys[key])

    async def expire(self, key: str, ttl: int) -> bool:
        return True


def _spec(max_per_hour: int = 1) -> TriggerSpec:
    spec = TriggerSpec(
        trigger_type=TriggerType.WEBHOOK, goal_template="go", max_firings_per_hour=max_per_hour
    )
    spec.trigger_id = "tr-order"  # type: ignore[attr-defined]
    return spec


def _dispatcher(redis: _Redis) -> tuple[TriggerDispatcher, Any]:
    gs = SimpleNamespace(create_goal=AsyncMock(return_value={"goal_id": "g"}))
    d = TriggerDispatcher(goal_service=gs, redis=redis)
    d._write_dlq = AsyncMock()  # type: ignore[method-assign]
    return d, gs


def _dedup_claims(redis: _Redis) -> list[str]:
    return [k for k in redis.set_calls if k.startswith("trigger_dedup:")]


def _rate_count(redis: _Redis) -> int:
    return sum(int(v) for k, v in redis.keys.items() if k.startswith("trigger_rate:"))


async def test_rate_limited_firing_never_claims_the_dedup_key() -> None:
    redis = _Redis()
    d, gs = _dispatcher(redis)
    first = await d.dispatch(_spec(1), {"n": 1}, CTX)
    assert first.goal_created is True
    claims_before = len(_dedup_claims(redis))

    throttled = await d.dispatch(_spec(1), {"n": 2}, CTX)

    assert throttled.skip_reason == "rate_limit"
    assert len(_dedup_claims(redis)) == claims_before  # no claim for the throttled one
    assert gs.create_goal.await_count == 1


async def test_throttled_firing_is_dead_lettered_for_replay() -> None:
    redis = _Redis()
    d, _ = _dispatcher(redis)
    await d.dispatch(_spec(1), {"n": 1}, CTX)
    await d.dispatch(_spec(1), {"n": 2}, CTX)
    d._write_dlq.assert_awaited()
    assert d._write_dlq.await_args.args[2] == "RATE_LIMITED"
    assert d._write_dlq.await_args.args[4] == {"n": 2}


async def test_caller_answering_429_suppresses_the_dead_letter() -> None:
    redis = _Redis()
    d, _ = _dispatcher(redis)
    await d.dispatch(_spec(1), {"n": 1}, CTX)
    event = await d.dispatch(_spec(1), {"n": 2}, CTX, dead_letter_throttled=False)
    assert event.skip_reason == "rate_limit"
    d._write_dlq.assert_not_awaited()


async def test_bulkhead_full_never_claims_and_is_dead_lettered() -> None:
    redis = _Redis()
    redis.keys["trigger_bulkhead:t1"] = 2  # free plan: 2 slots, both busy
    d, gs = _dispatcher(redis)
    event = await d.dispatch(_spec(0), {"n": 1}, CTX)
    assert event.skip_reason == "bulkhead_full"
    assert _dedup_claims(redis) == []
    gs.create_goal.assert_not_awaited()
    assert d._write_dlq.await_args.args[2] == "BULKHEAD_FULL"


async def test_duplicate_in_flight_is_skipped_without_spending_a_rate_token() -> None:
    redis = _Redis()
    d, gs = _dispatcher(redis)
    first = await d.dispatch(_spec(5), {"n": 1}, CTX)
    assert first.goal_created is True
    spent = _rate_count(redis)

    dup = await d.dispatch(_spec(5), {"n": 1}, CTX)

    assert dup.skip_reason == "dedup"
    assert _rate_count(redis) == spent
    assert gs.create_goal.await_count == 1


async def test_lost_claim_race_is_a_dedup_skip_and_frees_the_bulkhead() -> None:
    redis = _Redis()
    d, gs = _dispatcher(redis)
    real_set = redis.set

    async def racing_set(key: str, value: Any, ex: int = 0, nx: bool = False) -> Any:
        # Another replica claimed the key between the peek and the claim.
        if key.startswith("trigger_dedup:"):
            redis.keys[key] = 1
        return await real_set(key, value, ex=ex, nx=nx)

    redis.set = racing_set  # type: ignore[method-assign]
    event = await d.dispatch(_spec(5), {"n": 1}, CTX)
    assert event.skip_reason == "dedup"
    gs.create_goal.assert_not_awaited()
    assert int(redis.keys.get("trigger_bulkhead:t1", 0)) == 0
