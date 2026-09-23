"""Regression: HITL/memory triggers must not be dispatched twice per event.

``TriggerConsumerSupervisor`` starts three "core" pub/sub consumers together
(app/triggers/supervisor.py): ``ChainTriggerConsumer``, ``HITLTriggerConsumer``
and ``MemoryTriggerConsumer``. Each opens its own ``redis.pubsub()``
subscription, and Redis fans a published message out to *every* subscriber —
so if more than one consumer subscribes to the same channel, a single
real-world event (one HITL approval click, one memory write) is handled by
each subscribed consumer independently.

``ChainTriggerConsumer`` used to also list ``hitl.approved`` / ``hitl.rejected``
/ ``memory.created`` in its ``CHANNELS`` — the exact channels
``HITLTriggerConsumer`` / ``MemoryTriggerConsumer`` already own exclusively.
Worse, ``ChainTriggerConsumer.dispatch(...)`` passes ``source_goal_id`` /
``completion_event_id`` (so it derives one idempotency key) while
``HITLTriggerConsumer`` / ``MemoryTriggerConsumer`` pass neither (so they
derive a *different* key for the same event). Because the two consumers
computed different idempotency keys for the identical event, the Redis-backed
dedup in ``TriggerDispatcher._is_duplicate`` never saw a collision, and a
single HITL approval (or memory write) launched TWO autonomous goals instead
of one.

This test wires a real ``TriggerDispatcher`` (with an in-memory fake Redis
backing the atomic SETNX dedup, matching production's ``_is_duplicate``) and
feeds one "hitl.approved" pub/sub message to both a ``ChainTriggerConsumer``
and a ``HITLTriggerConsumer`` — modelling both consumers reacting to the one
Redis-fanned-out message — then asserts only one goal is created.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.triggers.consumers.chain import ChainTriggerConsumer
from app.triggers.consumers.hitl import HITLTriggerConsumer
from app.triggers.consumers.memory import MemoryTriggerConsumer
from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore


class _FakeAsyncRedis:
    """Minimal async Redis stand-in: only ``set`` (SETNX) is implemented, which
    is all ``TriggerDispatcher._is_duplicate`` needs. Every other method raises
    AttributeError, which the rate limiter / bulkhead already fail open on."""

    def __init__(self) -> None:
        self._store: dict[str, int] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self._store:
            return None  # already exists -> SETNX fails, mirrors real Redis
        self._store[key] = value
        return True


class _FakeGoalService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create_goal(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(goal_id=f"g-{len(self.calls)}")


def _make_dispatcher() -> tuple[TriggerDispatcher, _FakeGoalService]:
    goal_service = _FakeGoalService()
    dispatcher = TriggerDispatcher(
        goal_service=goal_service,
        db_session_factory=None,
        redis=_FakeAsyncRedis(),
    )
    return dispatcher, goal_service


def _hitl_message(*, goal_id: str, completion_event_id: str) -> dict:
    return {
        "type": "message",
        "channel": b"hitl.approved",
        "data": json.dumps(
            {
                "tenant_id": "t1",
                "goal_id": goal_id,
                "completion_event_id": completion_event_id,
                "hitl_queue_id": "",
            }
        ).encode(),
    }


@pytest.mark.asyncio
async def test_hitl_approved_event_is_not_double_dispatched_by_chain_and_hitl_consumers():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(
        trigger_type=TriggerType.HITL_APPROVED,
        goal_template="follow up on approval {{payload.goal_id}}",
    )
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template=spec.goal_template)

    dispatcher, goal_service = _make_dispatcher()
    chain_consumer = ChainTriggerConsumer(trigger_store=store, dispatcher=dispatcher)
    hitl_consumer = HITLTriggerConsumer(trigger_store=store, dispatcher=dispatcher)

    msg = _hitl_message(goal_id="g-001", completion_event_id="evt-001")

    # Model Redis fanning the SAME published message out to both subscribed
    # consumers, exactly as TriggerConsumerSupervisor runs them concurrently.
    await chain_consumer._handle(msg)
    await hitl_consumer._handle(msg)

    assert len(goal_service.calls) == 1, (
        "a single hitl.approved event dispatched a goal "
        f"{len(goal_service.calls)} times instead of once"
    )


@pytest.mark.asyncio
async def test_memory_created_event_is_not_double_dispatched_by_chain_and_memory_consumers():
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(
        trigger_type=TriggerType.MEMORY_CREATED,
        goal_template="review new memory {{payload.goal_id}}",
    )
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template=spec.goal_template)

    dispatcher, goal_service = _make_dispatcher()
    chain_consumer = ChainTriggerConsumer(trigger_store=store, dispatcher=dispatcher)
    memory_consumer = MemoryTriggerConsumer(trigger_store=store, dispatcher=dispatcher)

    msg = {
        "type": "message",
        "channel": b"memory.created",
        "data": json.dumps(
            {
                "tenant_id": "t1",
                "goal_id": "g-002",
                "completion_event_id": "evt-002",
                "memory_type": "",
            }
        ).encode(),
    }

    await chain_consumer._handle(msg)
    await memory_consumer._handle(msg)

    assert len(goal_service.calls) == 1, (
        "a single memory.created event dispatched a goal "
        f"{len(goal_service.calls)} times instead of once"
    )


@pytest.mark.asyncio
async def test_chain_consumer_actually_dispatches_goal_completed_trigger():
    """``find_by_type_async`` returns dict records with the TriggerSpec under a
    "spec" key. ``ChainTriggerConsumer`` used ``getattr(trigger, "spec",
    trigger)`` to pull it out — a no-op for a dict (dicts don't expose their
    keys as attributes) — so ``spec`` was always the raw dict, and
    ``TriggerDispatcher.dispatch()`` immediately raised ``AttributeError:
    'dict' object has no attribute 'trigger_type'``, silently caught and
    logged by ``_dispatch_matching``. GOAL_COMPLETED/GOAL_FAILED/
    GOAL_SCORE_BELOW chain triggers therefore never fired a single goal in
    production. This asserts a real goal is created end-to-end."""
    store = ScheduleStore()
    tc = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
    spec = TriggerSpec(
        trigger_type=TriggerType.GOAL_COMPLETED,
        goal_template="process completion of {{payload.goal_id}}",
    )
    store.create(spec=spec, tenant_ctx=tc, goal_id="g1", goal_template=spec.goal_template)

    dispatcher, goal_service = _make_dispatcher()
    chain_consumer = ChainTriggerConsumer(trigger_store=store, dispatcher=dispatcher)

    msg = {
        "type": "message",
        "channel": b"goal.completed",
        "data": json.dumps(
            {"tenant_id": "t1", "goal_id": "g-001", "agent_id": "", "score": 0.9}
        ).encode(),
    }
    await chain_consumer._handle(msg)

    assert len(goal_service.calls) == 1
    assert goal_service.calls[0]["goal_text"] == "process completion of g-001"


@pytest.mark.asyncio
async def test_chain_consumer_no_longer_subscribes_to_hitl_or_memory_channels():
    """CHANNELS must not overlap with the dedicated HITL/memory consumers'
    exclusive channels — that overlap is exactly what caused the double-fire."""
    assert "hitl.approved" not in ChainTriggerConsumer.CHANNELS
    assert "hitl.rejected" not in ChainTriggerConsumer.CHANNELS
    assert "memory.created" not in ChainTriggerConsumer.CHANNELS
