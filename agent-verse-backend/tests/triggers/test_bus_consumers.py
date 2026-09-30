"""TRG-18: trigger consumers read Redis Streams through consumer groups.

Pub/sub was at-most-once: an event published while the consumers were down (a
deploy, a Redis failover, a supervisor restart) was lost. Consumers now read
their family stream with one consumer group per consumer type, XACK after the
dispatcher accepted the event, and XAUTOCLAIM entries a crashed replica left
pending — so an event published while consumers are stopped is dispatched once
they start, exactly once per consumer type across N replicas.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import fakeredis
import pytest

from app.core.config import Settings
from app.tenancy.context import PlanTier, TenantContext
from app.triggers import bus
from app.triggers.consumers.chain import ChainTriggerConsumer, build_chain_event
from app.triggers.consumers.event import EventTriggerConsumer
from app.triggers.consumers.hitl import HITLTriggerConsumer
from app.triggers.consumers.memory import MemoryTriggerConsumer
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.supervisor import TriggerConsumerSupervisor

CTX = TenantContext(tenant_id="t-bus", plan=PlanTier.FREE, api_key_id="k")


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        trigger_bus_block_ms=20,
        trigger_bus_claim_idle_ms=0,
        trigger_bus_max_deliveries=3,
    )
    monkeypatch.setattr(bus, "get_settings", lambda: s)
    return s


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis(decode_responses=True)


class _Store:
    def __init__(self, specs: dict[str, list[Any]], *, fail: int = 0) -> None:
        self._specs = specs
        self.fail = fail  # raise on the first N lookups (store outage)

    async def find_by_type_async(self, trigger_type: str, tenant_id: str = "", **_: Any) -> list:
        if self.fail > 0:
            self.fail -= 1
            raise ConnectionError("db down")
        return [{"spec": s} for s in self._specs.get(trigger_type, [])]


def _dispatcher() -> Any:
    d = AsyncMock()
    d.resolve_tenant_plan = AsyncMock(return_value="free")
    return d


def _chain_spec() -> TriggerSpec:
    return TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED)


async def _goal_completed(redis: Any, goal_id: str) -> None:
    await bus.publish_trigger_event(
        redis,
        "goal.completed",
        build_chain_event(channel="goal.completed", tenant_id=CTX.tenant_id, goal_id=goal_id),
    )


async def _until(cond: Any, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


async def _run(consumer: Any) -> asyncio.Task[None]:
    return asyncio.create_task(consumer.start())


async def _stop(consumer: Any, task: asyncio.Task[None]) -> None:
    await consumer.stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _pending(redis: Any, stream: str, group: str) -> int:
    info = await redis.xpending(stream, group)
    return int(info["pending"])


async def test_event_published_while_consumer_stopped_is_dispatched_after_start(
    settings: Settings, redis: Any
) -> None:
    await _goal_completed(redis, "g-before-start")  # no consumer running yet
    disp = _dispatcher()
    consumer = ChainTriggerConsumer(
        trigger_store=_Store({"goal_completed": [_chain_spec()]}), dispatcher=disp, redis=redis
    )

    task = await _run(consumer)
    try:
        await _until(lambda: disp.dispatch.await_count == 1)
    finally:
        await _stop(consumer, task)

    kwargs = disp.dispatch.await_args.kwargs
    assert kwargs["completion_event_id"] == "g-before-start:goal.completed"
    assert await _pending(redis, settings.trigger_bus_stream_goal, ChainTriggerConsumer.GROUP) == 0


async def test_restarted_consumer_resumes_from_the_group_position(
    settings: Settings, redis: Any
) -> None:
    disp = _dispatcher()
    consumer = ChainTriggerConsumer(
        trigger_store=_Store({"goal_completed": [_chain_spec()]}), dispatcher=disp, redis=redis
    )
    task = await _run(consumer)
    await _goal_completed(redis, "g1")
    await _until(lambda: disp.dispatch.await_count == 1)
    await _stop(consumer, task)

    await _goal_completed(redis, "g2")  # published during the "deploy"
    fresh = ChainTriggerConsumer(
        trigger_store=_Store({"goal_completed": [_chain_spec()]}), dispatcher=disp, redis=redis
    )
    task = await _run(fresh)
    try:
        await _until(lambda: disp.dispatch.await_count == 2)
        await asyncio.sleep(0.05)
    finally:
        await _stop(fresh, task)
    ids = [c.kwargs["source_goal_id"] for c in disp.dispatch.await_args_list]
    assert ids == ["g1", "g2"]  # g1 not redelivered, g2 not lost


async def test_crashed_replicas_pending_entry_is_autoclaimed_and_acked(
    settings: Settings, redis: Any
) -> None:
    stream, group = settings.trigger_bus_stream_goal, ChainTriggerConsumer.GROUP
    await bus.TriggerStreamReader(redis, stream=stream, group=group).ensure_group()
    await _goal_completed(redis, "g-crash")
    # Replica A reads the entry and dies before dispatching / acking it.
    delivered = await redis.xreadgroup(group, "replica-a", {stream: ">"}, count=10)
    assert delivered and await _pending(redis, stream, group) == 1

    disp = _dispatcher()
    replica_b = ChainTriggerConsumer(
        trigger_store=_Store({"goal_completed": [_chain_spec()]}), dispatcher=disp, redis=redis
    )
    task = await _run(replica_b)
    try:
        await _until(lambda: disp.dispatch.await_count == 1)
        await asyncio.sleep(0.05)
    finally:
        await _stop(replica_b, task)
    assert disp.dispatch.await_args.kwargs["source_goal_id"] == "g-crash"
    assert await _pending(redis, stream, group) == 0


async def test_store_outage_leaves_entry_pending_and_it_is_retried(
    settings: Settings, redis: Any
) -> None:
    """The trigger lookup failing is not 'accepted': no XACK, retried later."""
    disp = _dispatcher()
    store = _Store({"goal_completed": [_chain_spec()]}, fail=1)
    consumer = ChainTriggerConsumer(trigger_store=store, dispatcher=disp, redis=redis)
    await _goal_completed(redis, "g-retry")

    task = await _run(consumer)
    try:
        await _until(lambda: disp.dispatch.await_count == 1)
    finally:
        await _stop(consumer, task)
    assert store.fail == 0
    stream = settings.trigger_bus_stream_goal
    assert await _pending(redis, stream, ChainTriggerConsumer.GROUP) == 0


async def test_poison_entry_is_dropped_after_max_deliveries(settings: Settings, redis: Any) -> None:
    disp = _dispatcher()
    store = _Store({"goal_completed": [_chain_spec()]}, fail=10_000)  # never recovers
    consumer = ChainTriggerConsumer(trigger_store=store, dispatcher=disp, redis=redis)
    await _goal_completed(redis, "g-poison")
    stream = settings.trigger_bus_stream_goal

    task = await _run(consumer)
    try:
        await _until(lambda: store.fail < 10_000)
        await asyncio.sleep(0.3)
        pending = await _pending(redis, stream, ChainTriggerConsumer.GROUP)
    finally:
        await _stop(consumer, task)
    assert pending == 0
    disp.dispatch.assert_not_awaited()
    assert 10_000 - store.fail <= settings.trigger_bus_max_deliveries + 1


async def test_n_replicas_dispatch_each_event_once_per_consumer_type(
    settings: Settings, redis: Any
) -> None:
    disp = _dispatcher()
    replicas = [
        ChainTriggerConsumer(
            trigger_store=_Store({"goal_completed": [_chain_spec()]}), dispatcher=disp, redis=redis
        )
        for _ in range(3)
    ]
    tasks = [await _run(r) for r in replicas]
    try:
        for i in range(6):
            await _goal_completed(redis, f"g{i}")
        await _until(lambda: disp.dispatch.await_count >= 6)
        await asyncio.sleep(0.1)
    finally:
        for r, t in zip(replicas, tasks, strict=True):
            await _stop(r, t)
    ids = sorted(c.kwargs["source_goal_id"] for c in disp.dispatch.await_args_list)
    # claim_idle_ms=0 lets a replica reclaim an entry another is mid-way
    # through; the dispatcher's idempotency key absorbs that in production.
    assert set(ids) == {f"g{i}" for i in range(6)}


async def test_each_consumer_type_has_its_own_group(settings: Settings, redis: Any) -> None:
    hitl_disp, memory_disp = _dispatcher(), _dispatcher()
    hitl = HITLTriggerConsumer(
        trigger_store=_Store(
            {"hitl_approved": [TriggerSpec(trigger_type=TriggerType.HITL_APPROVED)]}
        ),
        dispatcher=hitl_disp,
        redis=redis,
    )
    memory = MemoryTriggerConsumer(
        trigger_store=_Store(
            {"memory_created": [TriggerSpec(trigger_type=TriggerType.MEMORY_CREATED)]}
        ),
        dispatcher=memory_disp,
        redis=redis,
    )
    await bus.publish_trigger_event(
        redis,
        "hitl.approved",
        {"tenant_id": CTX.tenant_id, "request_id": "r1", "hitl_queue_id": ""},
    )
    await bus.publish_trigger_event(
        redis, "memory.created", {"tenant_id": CTX.tenant_id, "memory_id": "m1"}
    )
    tasks = [await _run(hitl), await _run(memory)]
    try:
        await _until(lambda: hitl_disp.dispatch.await_count == 1)
        await _until(lambda: memory_disp.dispatch.await_count == 1)
    finally:
        await _stop(hitl, tasks[0])
        await _stop(memory, tasks[1])
    assert hitl_disp.dispatch.await_args.args[1]["request_id"] == "r1"
    assert memory_disp.dispatch.await_args.args[1]["memory_id"] == "m1"


async def test_event_stream_consumers_each_see_every_event(settings: Settings, redis: Any) -> None:
    from app.triggers.consumers.condition import ConditionTriggerConsumer
    from app.triggers.consumers.conversational import (
        ConversationalTriggerConsumer,
        publish_conversational_event,
    )
    from app.triggers.consumers.event import publish_trigger_event

    event_disp, cond_disp, conv_disp = _dispatcher(), _dispatcher(), _dispatcher()
    store = _Store(
        {
            "event": [TriggerSpec(trigger_type=TriggerType.EVENT, event_channel="deploys")],
            "condition": [
                TriggerSpec(trigger_type=TriggerType.CONDITION, condition_expression="TRUE")
            ],
            "chat_keyword": [
                TriggerSpec(trigger_type=TriggerType.CHAT_KEYWORD, keyword_pattern="deploy")
            ],
        }
    )
    consumers = [
        EventTriggerConsumer(trigger_store=store, dispatcher=event_disp, redis=redis),
        ConditionTriggerConsumer(trigger_store=store, dispatcher=cond_disp, redis=redis),
        ConversationalTriggerConsumer(trigger_store=store, dispatcher=conv_disp, redis=redis),
    ]
    # Published before the consumers start.
    await publish_trigger_event(
        redis, event_channel="deploys", tenant_id=CTX.tenant_id, payload={"n": 2}
    )
    await publish_conversational_event(
        redis, tenant_id=CTX.tenant_id, event={"channel_type": "slack", "text": "deploy now"}
    )
    tasks = [await _run(c) for c in consumers]
    try:
        await _until(lambda: event_disp.dispatch.await_count == 1)
        await _until(lambda: cond_disp.dispatch.await_count >= 1)
        await _until(lambda: conv_disp.dispatch.await_count == 1)
    finally:
        for c, t in zip(consumers, tasks, strict=True):
            await _stop(c, t)
    groups = {g["name"] for g in await redis.xinfo_groups(settings.trigger_bus_stream_event)}
    assert {
        EventTriggerConsumer.GROUP,
        ConditionTriggerConsumer.GROUP,
        ConversationalTriggerConsumer.GROUP,
    } <= groups


class _FlakyRedis(fakeredis.FakeAsyncRedis):
    """XREADGROUP fails once (Redis failover), then works."""

    failures = 1

    async def xreadgroup(self, *args: Any, **kwargs: Any) -> Any:
        if self.failures:
            self.failures -= 1
            raise ConnectionError("Connection closed by server.")
        return await super().xreadgroup(*args, **kwargs)


async def test_redis_error_restarts_consumer_and_the_event_is_not_lost(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TRG-17's supervisor restart is kept; the stream keeps the event meanwhile."""
    redis = _FlakyRedis(decode_responses=True)
    disp = _dispatcher()
    sup = TriggerConsumerSupervisor(
        schedule_store=_Store({"goal_completed": [_chain_spec()]}),
        dispatcher=disp,
        redis=redis,
        restart_backoff_s=0.01,
        restart_backoff_max_s=0.02,
    )
    monkeypatch.setattr(
        sup,
        "_consumer_specs",
        lambda: [
            s
            for s in TriggerConsumerSupervisor._consumer_specs(sup)
            if s.name == "ChainTriggerConsumer"
        ],
    )
    await _goal_completed(redis, "g-failover")
    await sup.start()
    try:
        await _until(lambda: disp.dispatch.await_count == 1)
        assert sup.health()["ChainTriggerConsumer"]["restarts"] >= 1
    finally:
        await sup.stop()


async def test_end_to_end_goal_completed_to_dispatcher(settings: Settings, redis: Any) -> None:
    """GoalService goal_complete -> stream -> ChainTriggerConsumer (supervised)
    -> the real TriggerDispatcher creates the chained goal once."""
    from app.agent.state import GoalStatus
    from app.services.goal_service import GoalRecord, GoalService
    from app.triggers.dispatcher import TriggerDispatcher
    from app.triggers.store import ScheduleStore

    class _Goals:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def create_goal(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {"goal_id": f"chained-{len(self.calls)}"}

    store = ScheduleStore()
    store.create(
        spec=TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED, watch_agent_id="agent-1"),
        tenant_ctx=CTX,
        goal_id="",
        goal_template="follow up",
    )
    goals = _Goals()
    dispatcher = TriggerDispatcher(goal_service=goals, redis=redis)

    svc = GoalService()
    svc._redis = redis
    svc._goals["g-e2e"] = GoalRecord(
        goal_id="g-e2e",
        goal_text="g",
        status=GoalStatus.EXECUTING,
        tenant_id=CTX.tenant_id,
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
        agent_id="agent-1",
        execution_context={},
    )
    # Published before any consumer runs (e.g. during a deploy).
    await svc._dispatch_event("g-e2e", {"type": "goal_complete"}, tenant_ctx=CTX)

    sup = TriggerConsumerSupervisor(schedule_store=store, dispatcher=dispatcher, redis=redis)
    await sup.start()
    try:
        await _until(lambda: len(goals.calls) == 1)
        # A relay of the same terminal event (another replica) fires nothing new.
        await bus.publish_trigger_event(
            redis,
            "goal.completed",
            build_chain_event(
                channel="goal.completed",
                tenant_id=CTX.tenant_id,
                goal_id="g-e2e",
                agent_id="agent-1",
            ),
        )
        await asyncio.sleep(0.2)
    finally:
        await sup.stop()
    assert len(goals.calls) == 1
    assert goals.calls[0]["trigger_chain_depth"] == 1
    entries = await redis.xrange(settings.trigger_bus_stream_goal)
    assert json.loads(entries[0][1]["data"])["goal_id"] == "g-e2e"
