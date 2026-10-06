"""B2-OPEN-2: a DLQ retry replays the ORIGINAL firing under its dedup key.

A throttled firing is dead-lettered; ``POST /triggers/dlq/{id}/retry`` used to
dispatch it with its own per-attempt key (``dlq-retry:<id>:<n>``). If the
sender also redelivered the same event (same delivery id) once the throttle
lifted, both the redelivery and the DLQ retry created a goal. The DLQ row now
records the firing's dedup key and the retry dispatches under it, so whichever
of the two runs first wins and the other is a ``dedup`` no-op.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from app.tenancy.context import PlanTier, TenantContext
from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.models import TriggerSpec, TriggerType
from tests.triggers.test_dispatcher_gate_order import _Redis

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")


def _spec() -> TriggerSpec:
    spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK, goal_template="go", max_firings_per_hour=1)
    spec.trigger_id = "tr-replay"  # type: ignore[attr-defined]
    return spec


def _dispatcher(redis: _Redis) -> tuple[TriggerDispatcher, Any]:
    gs = SimpleNamespace(create_goal=AsyncMock(return_value={"goal_id": "g"}))
    d = TriggerDispatcher(goal_service=gs, redis=redis)
    d._write_dlq = AsyncMock()  # type: ignore[method-assign]
    return d, gs


def _lift_rate_limit(redis: _Redis) -> None:
    for key in [k for k in redis.keys if k.startswith("trigger_rate:")]:
        del redis.keys[key]


async def _throttled_dlq_key(d: TriggerDispatcher, redis: _Redis) -> str:
    """Spend the hourly token, then get delivery ``evt-2`` throttled + dead-lettered."""
    await d.dispatch(_spec(), {"n": 1}, CTX, message_id="delivery:evt-1")
    throttled = await d.dispatch(_spec(), {"n": 2}, CTX, message_id="delivery:evt-2")
    assert throttled.skip_reason == "rate_limit"
    key = d._write_dlq.await_args.kwargs["idempotency_key"]  # type: ignore[attr-defined]
    assert key == throttled.idempotency_key and key
    _lift_rate_limit(redis)
    return str(key)


async def test_dlq_row_records_the_key_a_redelivery_would_use() -> None:
    redis = _Redis()
    d, _ = _dispatcher(redis)
    key = await _throttled_dlq_key(d, redis)
    redelivery = await d.dispatch(_spec(), {"n": 2}, CTX, message_id="delivery:evt-2")
    assert redelivery.idempotency_key == key


async def test_retry_after_a_successful_redelivery_is_a_no_op() -> None:
    redis = _Redis()
    d, gs = _dispatcher(redis)
    key = await _throttled_dlq_key(d, redis)

    redelivery = await d.dispatch(_spec(), {"n": 2}, CTX, message_id="delivery:evt-2")
    assert redelivery.goal_created is True
    _lift_rate_limit(redis)
    retry = await d.dispatch(_spec(), {"n": 2}, CTX, idempotency_key=key)

    assert retry.skip_reason == "dedup"
    assert gs.create_goal.await_count == 2  # evt-1 and evt-2 once each


async def test_redelivery_after_a_successful_retry_is_a_no_op() -> None:
    redis = _Redis()
    d, gs = _dispatcher(redis)
    key = await _throttled_dlq_key(d, redis)

    retry = await d.dispatch(_spec(), {"n": 2}, CTX, idempotency_key=key)
    assert retry.goal_created is True and retry.idempotency_key == key
    _lift_rate_limit(redis)
    redelivery = await d.dispatch(_spec(), {"n": 2}, CTX, message_id="delivery:evt-2")

    assert redelivery.skip_reason == "dedup"
    assert gs.create_goal.await_count == 2


async def test_gate_unavailable_and_enqueue_failure_dead_letter_with_the_key() -> None:
    from app.triggers.dispatcher import TriggerGateUnavailableError

    redis = _Redis()
    d, gs = _dispatcher(redis)
    d._rate_limiter.check = AsyncMock(  # type: ignore[method-assign]
        side_effect=TriggerGateUnavailableError("redis down")
    )
    skipped = await d.dispatch(_spec(), {"n": 3}, CTX, message_id="delivery:evt-3")
    assert skipped.skip_reason == "rate_limit_unavailable"
    assert d._write_dlq.await_args.kwargs["idempotency_key"] == skipped.idempotency_key  # type: ignore[attr-defined]

    redis2 = _Redis()
    d2, gs2 = _dispatcher(redis2)
    gs2.create_goal.side_effect = RuntimeError("queue down")
    failed = await d2.dispatch(_spec(), {"n": 4}, CTX, message_id="delivery:evt-4")
    assert failed.goal_created is False
    dlq_key = d2._write_dlq.await_args.kwargs["idempotency_key"]  # type: ignore[attr-defined]
    # The failed attempt released its claim, so the retry under that key fires.
    gs2.create_goal.side_effect = None
    _lift_rate_limit(redis2)
    retry = await d2.dispatch(_spec(), {"n": 4}, CTX, idempotency_key=dlq_key)
    assert retry.goal_created is True
