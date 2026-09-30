"""TRG-19: counter / window / state-transition conditions share state in Redis.

Counters, windows and last states lived in a per-process dict while every replica
receives every event, so thresholds fired once per replica (at different events)
or never after a restart. State now lives in Redis keyed by tenant + trigger, and
each event is applied once (claimed by event id) however many replicas see it.
"""

from __future__ import annotations

import json
from typing import Any

import fakeredis.aioredis
import pytest

from app.triggers.consumers.condition import ConditionTriggerConsumer
from app.triggers.consumers.event import event_channel_name, publish_trigger_event
from app.triggers.models import TriggerSpec, TriggerType


class _Store:
    def __init__(self, by_type: dict[str, list[Any]]) -> None:
        self._t = by_type

    async def find_by_type_async(self, ttype: str, *, tenant_id: str, **_: Any) -> list[Any]:
        return self._t.get(ttype, [])


class _Dispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any], str]] = []

    async def resolve_tenant_plan(self, tenant_id: str) -> str:
        return "free"

    async def dispatch(self, spec: Any, payload: Any, tenant_ctx: Any, *, message_id: str = "") -> None:
        self.calls.append((spec, payload, message_id))


def _msg(payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "pmessage", "channel": event_channel_name("metrics"),
            "data": json.dumps(payload)}


def _counter_spec(threshold: int = 5) -> TriggerSpec:
    spec = TriggerSpec(trigger_type=TriggerType.COUNTER_THRESHOLD, counter_key="errors",
                       counter_threshold=threshold, counter_window_secs=3600)
    spec.trigger_id = "tr-counter"  # type: ignore[attr-defined]
    return spec


@pytest.fixture
def redis() -> Any:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


async def test_two_replicas_seeing_the_same_five_events_fire_exactly_once(redis) -> None:
    store = _Store({"counter_threshold": [{"spec": _counter_spec(5)}]})
    dispatcher = _Dispatcher()
    replicas = [
        ConditionTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=redis)
        for _ in range(2)
    ]
    for i in range(5):
        event = _msg({"tenant_id": "t1", "event_id": f"e{i}"})
        for replica in replicas:  # pub/sub fans every event out to every replica
            await replica._handle(event)
    assert len(dispatcher.calls) == 1
    assert dispatcher.calls[0][2] == "e4"


async def test_counter_state_survives_a_consumer_restart(redis) -> None:
    store = _Store({"counter_threshold": [{"spec": _counter_spec(5)}]})
    dispatcher = _Dispatcher()
    first = ConditionTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=redis)
    for i in range(3):
        await first._handle(_msg({"tenant_id": "t1", "event_id": f"e{i}"}))
    restarted = ConditionTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=redis)
    for i in range(3, 5):
        await restarted._handle(_msg({"tenant_id": "t1", "event_id": f"e{i}"}))
    assert len(dispatcher.calls) == 1


async def test_counters_are_isolated_per_tenant(redis) -> None:
    store = _Store({"counter_threshold": [{"spec": _counter_spec(2)}]})
    dispatcher = _Dispatcher()
    c = ConditionTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=redis)
    await c._handle(_msg({"tenant_id": "t1", "event_id": "a"}))
    await c._handle(_msg({"tenant_id": "t2", "event_id": "b"}))
    assert dispatcher.calls == []


async def test_window_aggregate_counts_each_event_once_across_replicas(redis) -> None:
    spec = TriggerSpec(trigger_type=TriggerType.WINDOW_AGGREGATE, window_field="latency",
                       window_threshold=250.0, window_aggregation="sum", window_seconds=300)
    spec.trigger_id = "tr-window"  # type: ignore[attr-defined]
    store = _Store({"window_aggregate": [{"spec": spec}]})
    dispatcher = _Dispatcher()
    replicas = [ConditionTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=redis)
                for _ in range(3)]
    for i, value in enumerate((100, 100)):
        for r in replicas:
            await r._handle(_msg({"tenant_id": "t1", "event_id": f"w{i}", "latency": value}))
    # 3 replicas x (100 + 100) would be 600 if each counted each event; it is 200.
    assert dispatcher.calls == []
    for r in replicas:
        await r._handle(_msg({"tenant_id": "t1", "event_id": "w2", "latency": 100}))
    assert len(dispatcher.calls) == 1


async def test_state_transition_last_state_is_shared(redis) -> None:
    spec = TriggerSpec(trigger_type=TriggerType.STATE_TRANSITION, from_state="open",
                       to_state="closed")
    spec.trigger_id = "tr-state"  # type: ignore[attr-defined]
    store = _Store({"state_transition": [{"spec": spec}]})
    dispatcher = _Dispatcher()
    a = ConditionTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=redis)
    b = ConditionTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=redis)
    await a._handle(_msg({"tenant_id": "t1", "event_id": "s1", "entity_id": "x", "state": "open"}))
    # Replica b never saw "open" itself; the shared last state makes this a transition.
    await b._handle(_msg({"tenant_id": "t1", "event_id": "s2", "entity_id": "x", "state": "closed"}))
    assert len(dispatcher.calls) == 1


async def test_published_events_carry_a_server_event_id(redis) -> None:
    class _Capture:
        def __init__(self) -> None:
            self.bodies: list[dict[str, Any]] = []

        async def publish(self, channel: str, data: str) -> int:
            self.bodies.append(json.loads(data))
            return 1

        async def xadd(self, stream: str, fields: dict[str, str], **_: Any) -> str:
            return "1-0"  # TRG-18 durable stream copy (bodies checked via publish)

    cap = _Capture()
    await publish_trigger_event(cap, event_channel="metrics", tenant_id="t1", payload={"x": 1})
    await publish_trigger_event(cap, event_channel="metrics", tenant_id="t1", payload={"x": 1})
    ids = [b.get("event_id") for b in cap.bodies]
    assert all(ids) and ids[0] != ids[1]  # identical payloads are still distinct events
