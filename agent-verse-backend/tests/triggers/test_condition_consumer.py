"""2.W-1 / Family D: condition/state triggers evaluate on the EVENT bus."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.triggers.consumers.condition import ConditionTriggerConsumer
from app.triggers.consumers.event import event_channel_name


def _consumer() -> ConditionTriggerConsumer:
    return ConditionTriggerConsumer(trigger_store=None, dispatcher=None, redis=None)


def _spec(**kw: Any) -> Any:
    base = {
        "trigger_id": "t-1",
        "condition_expression": "",
        "condition": "",
        "counter_key": "",
        "counter_threshold": 0,
        "counter_window_secs": 3600,
        "window_field": "",
        "window_threshold": 0.0,
        "window_aggregation": "sum",
        "window_seconds": 300,
        "from_state": "",
        "to_state": "",
        "compound_logic": "AND",
        "compound_trigger_ids": [],
    }
    base.update(kw)
    return SimpleNamespace(**base)


# ── CONDITION ─────────────────────────────────────────────────────────────────


def test_condition_fires_when_cel_true() -> None:
    c = _consumer()
    c._cel = SimpleNamespace(evaluate=lambda expr, payload: True)  # type: ignore[assignment]
    assert c._should_fire(
        "condition", _spec(condition_expression="payload.x == '1'"), "t-1", {}, {}
    )


def test_condition_no_fire_when_cel_false() -> None:
    c = _consumer()
    c._cel = SimpleNamespace(evaluate=lambda expr, payload: False)  # type: ignore[assignment]
    assert not c._should_fire("condition", _spec(condition_expression="x"), "t-1", {}, {})


# ── COUNTER_THRESHOLD ─────────────────────────────────────────────────────────


def test_counter_fires_at_threshold_then_resets() -> None:
    c = _consumer()
    spec = _spec(counter_threshold=2)
    assert c._should_fire("counter_threshold", spec, "t-1", {}, {}) is False  # count 1
    assert c._should_fire("counter_threshold", spec, "t-1", {}, {}) is True  # count 2 → fire
    # After firing the window resets, so it takes two more to fire again.
    assert c._should_fire("counter_threshold", spec, "t-1", {}, {}) is False


# ── WINDOW_AGGREGATE ──────────────────────────────────────────────────────────


def test_window_aggregate_fires_when_sum_exceeds() -> None:
    c = _consumer()
    spec = _spec(window_field="value", window_aggregation="sum", window_threshold=10.0)
    assert c._should_fire("window_aggregate", spec, "t-1", {"value": 6}, {}) is False  # sum 6
    assert c._should_fire("window_aggregate", spec, "t-1", {"value": 6}, {}) is True  # sum 12 > 10


def test_window_ignores_non_numeric_field() -> None:
    c = _consumer()
    spec = _spec(window_field="value", window_aggregation="sum", window_threshold=1.0)
    assert c._should_fire("window_aggregate", spec, "t-1", {"value": "NaNish"}, {}) is False


# ── STATE_TRANSITION ──────────────────────────────────────────────────────────


def test_state_transition_fires_on_from_to() -> None:
    c = _consumer()
    spec = _spec(from_state="open", to_state="closed")
    # open (prev None → no to-match), then closed (open→closed matches) fires.
    assert c._should_fire("state_transition", spec, "t-1", {"state": "open"}, {}) is False
    assert c._should_fire("state_transition", spec, "t-1", {"state": "closed"}, {}) is True


def test_state_transition_no_fire_without_state() -> None:
    c = _consumer()
    assert c._should_fire("state_transition", _spec(), "t-1", {}, {}) is False


# ── COMPOUND ──────────────────────────────────────────────────────────────────


def test_compound_or_fires_when_any_subcondition_true() -> None:
    c = _consumer()
    c._cel = SimpleNamespace(  # sub "a" true, sub "b" false
        evaluate=lambda expr, payload: expr == "TRUE"
    )  # type: ignore[assignment]
    index = {"a": _spec(condition_expression="TRUE"), "b": _spec(condition_expression="FALSE")}
    spec = _spec(compound_logic="OR", compound_trigger_ids=["a", "b"])
    assert c._should_fire("compound", spec, "t-1", {}, index) is True


def test_compound_and_no_fire_when_one_false() -> None:
    c = _consumer()
    c._cel = SimpleNamespace(evaluate=lambda expr, payload: expr == "TRUE")  # type: ignore[assignment]
    index = {"a": _spec(condition_expression="TRUE"), "b": _spec(condition_expression="FALSE")}
    spec = _spec(compound_logic="AND", compound_trigger_ids=["a", "b"])
    assert c._should_fire("compound", spec, "t-1", {}, index) is False


# ── Dispatch flow via the event bus ───────────────────────────────────────────


class _FakePubSub:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = messages

    async def psubscribe(self, pattern: str) -> None:
        self.pattern = pattern

    async def listen(self) -> Any:
        for m in self._messages:
            await asyncio.sleep(0)
            yield m


class _FakeRedis:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = messages

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self._messages)


class _FakeStore:
    def __init__(self, by_type: dict[str, list[dict[str, Any]]]) -> None:
        self._t = by_type

    async def find_by_type_async(self, ttype: str, *, tenant_id: str) -> list[dict[str, Any]]:
        return self._t.get(ttype, [])


class _FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def dispatch(
        self, spec: Any, payload: Any, tenant_ctx: Any, *, message_id: str = ""
    ) -> None:
        self.calls.append((spec, payload, tenant_ctx, message_id))


@pytest.mark.asyncio
async def test_condition_consumer_dispatches_on_event() -> None:
    msg = {
        "type": "pmessage",
        "channel": event_channel_name("metrics").encode(),
        "data": json.dumps({"tenant_id": "t1", "value": 99}).encode(),
    }
    store = _FakeStore({"condition": [{"spec": _spec(condition_expression="")}]})  # empty → True
    disp = _FakeDispatcher()
    c = ConditionTriggerConsumer(trigger_store=store, dispatcher=disp, redis=_FakeRedis([msg]))
    await c.start()
    assert len(disp.calls) == 1


@pytest.mark.asyncio
async def test_condition_consumer_ignores_untenanted_event() -> None:
    msg = {
        "type": "pmessage",
        "channel": event_channel_name("metrics").encode(),
        "data": json.dumps({"value": 1}).encode(),
    }
    store = _FakeStore({"condition": [{"spec": _spec(condition_expression="")}]})
    disp = _FakeDispatcher()
    c = ConditionTriggerConsumer(trigger_store=store, dispatcher=disp, redis=_FakeRedis([msg]))
    await c.start()
    assert disp.calls == []
