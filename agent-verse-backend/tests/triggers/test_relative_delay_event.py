"""B1-8: relative_delay can count from the triggering event.

``relative_delay`` only supported a fixed ``fire_at_iso`` base, and
``relative_to_field`` was refused ("not supported yet"): "follow up 30 minutes
after each support escalation" or "2 days after order.delivered_at" could not
be expressed. A relative_delay with an ``event_channel`` now arms one fire per
event on that channel, due ``relative_offset_seconds`` after the event or after
the timestamp at ``relative_to_field`` in its payload.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.scaling import tasks
from app.triggers.consumers.event import EventTriggerConsumer
from app.triggers.delayed import delayed_due_at, delayed_fire_id, is_event_armed
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.validation import validate_spec

T0 = dt.datetime(2026, 10, 6, 10, 0, tzinfo=dt.UTC)


def _spec(**kw: Any) -> TriggerSpec:
    return TriggerSpec(trigger_type=TriggerType.RELATIVE_DELAY, **kw)


# ── validation ────────────────────────────────────────────────────────────────


def test_an_event_relative_delay_is_valid() -> None:
    validate_spec(_spec(event_channel="support.escalated", relative_offset_seconds=1800))
    validate_spec(
        _spec(event_channel="orders.delivered", relative_offset_seconds=172800,
              relative_to_field="order.delivered_at")
    )


@pytest.mark.parametrize(
    ("kw", "match"),
    [
        ({}, "event_channel"),
        ({"event_channel": "a", "fire_at_iso": "2026-10-06T10:00:00"}, "not both"),
        ({"event_channel": "bad channel!"}, "event_channel"),
        ({"event_channel": "a", "relative_offset_seconds": -5}, "relative_offset_seconds"),
        ({"event_channel": "a", "relative_offset_seconds": 400 * 86400},
         "relative_offset_seconds"),
        ({"event_channel": "a", "relative_to_field": "order[0].x"}, "relative_to_field"),
    ],
)
def test_an_invalid_relative_delay_is_refused(kw: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        validate_spec(_spec(**kw))


def test_a_fixed_base_relative_delay_still_works() -> None:
    validate_spec(_spec(fire_at_iso="2026-10-06T10:00:00Z", relative_offset_seconds=60))


# ── due time ──────────────────────────────────────────────────────────────────


def test_due_counts_from_the_event_or_from_the_payload_field() -> None:
    assert delayed_due_at(offset_seconds=1800, relative_to_field="", data={},
                          received_at=T0) == T0 + dt.timedelta(minutes=30)
    data = {"order": {"delivered_at": "2026-10-04T08:15:00+05:30"}}
    assert delayed_due_at(offset_seconds=86400, relative_to_field="order.delivered_at",
                          data=data, received_at=T0) == dt.datetime(
        2026, 10, 5, 2, 45, tzinfo=dt.UTC)
    assert delayed_due_at(offset_seconds=60, relative_to_field="order.missing", data=data,
                          received_at=T0) is None
    assert delayed_due_at(offset_seconds=60, relative_to_field="ts",
                          data={"ts": "yesterday"}, received_at=T0) is None


def test_the_fire_id_is_stable_per_trigger_and_event() -> None:
    assert delayed_fire_id("s1", "e1") == delayed_fire_id("s1", "e1")
    assert delayed_fire_id("s1", "e1") != delayed_fire_id("s1", "e2")
    assert delayed_fire_id("s1", "e1") != delayed_fire_id("s2", "e1")


# ── the beat never evaluates an event-armed delay as a one-shot ───────────────


def test_an_event_armed_delay_is_never_due_by_itself() -> None:
    sched = {"trigger_type": "relative_delay", "event_channel": "support.escalated",
             "fire_at_iso": "", "relative_offset_seconds": 1800}
    now = dt.datetime(2026, 10, 6, 10, 0)
    assert tasks._time_trigger_slots(sched, now) == []
    assert tasks._next_evaluation_at(sched, now) == tasks._NEVER
    assert is_event_armed("relative_delay", "support.escalated", "")
    assert not is_event_armed("relative_delay", "support.escalated", "2026-10-06T10:00:00")


# ── the EVENT consumer arms matching triggers ─────────────────────────────────


class _Store:
    def __init__(self, by_type: dict[str, list[dict[str, Any]]]) -> None:
        self.by_type = by_type
        self.armed: list[dict[str, Any]] = []

    async def find_by_type_async(self, trigger_type: str, *, tenant_id: str,
                                 strict: bool = False) -> list[dict[str, Any]]:
        return [t for t in self.by_type.get(trigger_type, []) if t["tenant_id"] == tenant_id]

    async def arm_delayed_fire_async(self, **kw: Any) -> bool:
        self.armed.append(kw)
        return True


def _rec(sid: str, tenant: str, spec: TriggerSpec) -> dict[str, Any]:
    return {"schedule_id": sid, "tenant_id": tenant, "spec": spec, "paused": False}


@pytest.mark.asyncio
async def test_an_event_arms_each_matching_delay_of_its_tenant_only() -> None:
    follow_up = _spec(event_channel="support.escalated", relative_offset_seconds=1800)
    by_field = _spec(event_channel="support.escalated", relative_offset_seconds=600,
                     relative_to_field="ticket.opened_at")
    other_channel = _spec(event_channel="orders.delivered", relative_offset_seconds=60)
    fixed = _spec(fire_at_iso="2026-10-06T10:00:00Z")
    store = _Store({
        "relative_delay": [
            _rec("s1", "t1", follow_up), _rec("s2", "t1", by_field),
            _rec("s3", "t1", other_channel), _rec("s4", "t1", fixed),
            _rec("s5", "t2", _spec(event_channel="support.escalated")),
        ],
    })
    dispatcher = SimpleNamespace(dispatch=AsyncMock(), resolve_tenant_plan=AsyncMock())
    consumer = EventTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=None)
    data = {"tenant_id": "t1", "event_channel": "support.escalated", "event_id": "ev-1",
            "ticket": {"opened_at": "2026-10-06T09:00:00Z"}}
    before = dt.datetime.now(dt.UTC)
    await consumer._dispatch_matching("support.escalated", data)
    armed = {a["schedule_id"]: a for a in store.armed}
    assert set(armed) == {"s1", "s2"}
    assert all(a["tenant_id"] == "t1" and a["event_id"] == "ev-1" for a in armed.values())
    assert armed["s1"]["payload"] is data
    assert before + dt.timedelta(minutes=30) <= armed["s1"]["due_at"] <= (
        dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30))
    assert armed["s2"]["due_at"] == dt.datetime(2026, 10, 6, 9, 10, tzinfo=dt.UTC)
    dispatcher.dispatch.assert_not_awaited()  # no EVENT trigger matched


@pytest.mark.asyncio
async def test_an_event_without_the_configured_field_arms_nothing() -> None:
    spec = _spec(event_channel="c", relative_offset_seconds=60, relative_to_field="a.b")
    store = _Store({"relative_delay": [_rec("s1", "t1", spec)]})
    consumer = EventTriggerConsumer(trigger_store=store, dispatcher=SimpleNamespace(
        dispatch=AsyncMock(), resolve_tenant_plan=AsyncMock()), redis=None)
    await consumer._dispatch_matching("c", {"tenant_id": "t1", "event_id": "e"})
    assert store.armed == []
