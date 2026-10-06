"""a06-F103-02: the trigger bulkhead bounds in-flight trigger GOALS, not dispatch calls.

The Redis slot was taken before enqueue and released in the ``finally`` right
after it, so it only bounded concurrent ``dispatch()`` calls: a free tenant
(2 concurrent in-flight trigger goals per the spec's plan table) could have any
number of trigger goals running at once. The gate now adds the tenant's
non-terminal trigger goals (``trigger_events`` ⋈ ``goals``, read from Postgres so
every replica agrees) to the in-dispatch count.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from app.tenancy.context import PlanTier, TenantContext
from app.triggers.bulkhead import TriggerBulkhead, in_flight_window_seconds
from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.models import TriggerSpec, TriggerType
from tests.triggers.test_dispatcher_gate_order import _Redis

FREE = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")


def _spec() -> TriggerSpec:
    spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK, goal_template="go")
    spec.trigger_id = "tr-bh"  # type: ignore[attr-defined]
    return spec


def _dispatcher(redis: _Redis, in_flight: int | Exception) -> tuple[TriggerDispatcher, Any]:
    gs = SimpleNamespace(create_goal=AsyncMock(return_value={"goal_id": "g"}))
    d = TriggerDispatcher(goal_service=gs, redis=redis, db_session_factory=object())
    d._write_dlq = AsyncMock()  # type: ignore[method-assign]
    d._goal_outcomes_open = AsyncMock(return_value=False)  # type: ignore[method-assign]
    d._already_fired = AsyncMock(return_value=False)  # type: ignore[method-assign]
    d._persist_event = AsyncMock()  # type: ignore[method-assign]
    if isinstance(in_flight, Exception):
        reader = AsyncMock(side_effect=in_flight)
    else:
        reader = AsyncMock(return_value=in_flight)
    d._read_in_flight = reader  # type: ignore[method-assign]
    return d, gs


async def test_acquire_counts_in_flight_goals_against_the_cap() -> None:
    redis = _Redis()
    bh = TriggerBulkhead(redis=redis)
    # free cap 2: one goal running + this dispatch = 2 -> admitted
    assert await bh.acquire("t1", "free", in_flight=1) is True
    await bh.release("t1")
    # two goals running: this dispatch would be the third -> refused, slot returned
    assert await bh.acquire("t1", "free", in_flight=2) is False
    assert int(redis.keys["trigger_bulkhead:t1"]) == 0


async def test_free_tenant_with_two_running_trigger_goals_is_bulkhead_full() -> None:
    redis = _Redis()
    d, gs = _dispatcher(redis, in_flight=2)
    event = await d.dispatch(_spec(), {"n": 1}, FREE, message_id="m1")
    assert event.skip_reason == "bulkhead_full"
    gs.create_goal.assert_not_awaited()
    assert d._write_dlq.await_args.args[2] == "BULKHEAD_FULL"
    assert int(redis.keys["trigger_bulkhead:t1"]) == 0  # nothing leaked
    d._read_in_flight.assert_awaited_with("t1", in_flight_window_seconds("free"))


async def test_a_free_slot_fires() -> None:
    redis = _Redis()
    d, gs = _dispatcher(redis, in_flight=1)
    event = await d.dispatch(_spec(), {"n": 1}, FREE, message_id="m2")
    assert event.skip_reason is None
    gs.create_goal.assert_awaited_once()
    assert int(redis.keys["trigger_bulkhead:t1"]) == 0  # dispatch slot released


async def test_unreadable_in_flight_count_does_not_block_firing() -> None:
    """Like the outcome circuit, an availability guard: a read error is logged and
    only the in-dispatch count applies (Redis errors still fail closed)."""
    redis = _Redis()
    d, gs = _dispatcher(redis, in_flight=ConnectionError("pg down"))
    event = await d.dispatch(_spec(), {"n": 1}, FREE, message_id="m3")
    assert event.skip_reason is None
    gs.create_goal.assert_awaited_once()


async def test_no_db_factory_keeps_the_dispatch_only_bound() -> None:
    redis = _Redis()
    gs = SimpleNamespace(create_goal=AsyncMock(return_value={"goal_id": "g"}))
    d = TriggerDispatcher(goal_service=gs, redis=redis)
    d._write_dlq = AsyncMock()  # type: ignore[method-assign]
    assert await d._in_flight_trigger_goals("t1", "free") == 0


def test_window_covers_the_plans_goal_timeout() -> None:
    assert in_flight_window_seconds("free") == 3600 + 3600
    assert in_flight_window_seconds("enterprise") == 86_400 + 3600
    assert in_flight_window_seconds("bogus") == in_flight_window_seconds("free")
