"""TRG-55: event consumers never ack an event their triggers could not be read for.

Consumers called ``find_by_type_async`` non-strict: on a DB error the store fell
back to this replica's (partial, possibly empty) cache, the consumer matched the
event against that, found nothing, returned normally — and the stream entry was
XACKed. The firing was lost. Every consumer now reads strictly, so a DB outage
raises ``ScheduleStoreUnavailableError``, the entry stays pending and is retried
(XAUTOCLAIM) once the database is back; one that never succeeds is dead-lettered.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any
from unittest.mock import AsyncMock

import fakeredis
import pytest

from app.core.config import Settings
from app.triggers import bus
from app.triggers.consumers.chain import ChainTriggerConsumer, build_chain_event
from app.triggers.store import ScheduleStore, ScheduleStoreUnavailableError


class _FlakyDb:
    """A session factory whose sessions fail while ``down`` is set."""

    def __init__(self) -> None:
        self.down = True
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        if self.down:
            raise ConnectionError("postgres is down")
        raise AssertionError("not reached in these tests")


@pytest.mark.asyncio
async def test_strict_lookup_raises_instead_of_serving_the_cache() -> None:
    store = ScheduleStore(db_session_factory=_FlakyDb())
    assert await store.find_by_type_async("event", tenant_id="t") == []  # old: silent cache
    with pytest.raises(ScheduleStoreUnavailableError):
        await store.find_by_type_async("event", tenant_id="t", strict=True)


_CONSUMER_FILES = [
    "app/triggers/consumers/chain.py",
    "app/triggers/consumers/condition.py",
    "app/triggers/consumers/conversational.py",
    "app/triggers/consumers/event.py",
    "app/triggers/consumers/hitl.py",
    "app/triggers/consumers/memory.py",
    "app/triggers/channels/gateway.py",
]


@pytest.mark.parametrize("path", _CONSUMER_FILES)
def test_every_consumer_lookup_is_strict(path: str) -> None:
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / path).read_text()
    calls = re.findall(r"find_by_type_async\(([^()]*)\)", src, re.S)
    assert calls, f"{path}: no trigger lookups found"
    assert all("strict=True" in c for c in calls), f"{path}: non-strict lookup"


class _RecordingStore:
    """Raises like ScheduleStore does on a strict read during an outage, and
    serves an empty 'cache' to a non-strict one (the lost-firing case)."""

    def __init__(self) -> None:
        self.down = True
        self.spec: Any = None

    async def find_by_type_async(
        self, trigger_type: str, *, tenant_id: str, strict: bool = False, **_: Any
    ) -> list[dict[str, Any]]:
        if self.down:
            if strict:
                raise ScheduleStoreUnavailableError("db down")
            return []
        return [{"spec": self.spec, "schedule_id": "s1"}]


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        trigger_bus_block_ms=20,
        trigger_bus_claim_idle_ms=0,
        trigger_bus_max_deliveries=1_000,
    )
    monkeypatch.setattr(bus, "get_settings", lambda: s)
    return s


async def _until(cond: Any, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_db_outage_leaves_the_entry_pending_and_it_fires_after_recovery(
    settings: Settings,
) -> None:
    from app.triggers.models import TriggerSpec, TriggerType

    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    store = _RecordingStore()
    store.spec = TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED)
    disp = AsyncMock()
    disp.resolve_tenant_plan = AsyncMock(return_value="free")
    consumer = ChainTriggerConsumer(trigger_store=store, dispatcher=disp, redis=redis)
    await bus.publish_trigger_event(
        redis,
        "goal.completed",
        build_chain_event(channel="goal.completed", tenant_id="t1", goal_id="g-1"),
    )
    stream, group = settings.trigger_bus_stream_goal, ChainTriggerConsumer.GROUP
    task = asyncio.create_task(consumer.start())
    try:
        await asyncio.sleep(0.2)  # several delivery attempts during the outage
        assert int((await redis.xpending(stream, group))["pending"]) == 1
        disp.dispatch.assert_not_awaited()
        store.down = False  # the database is back
        await _until(lambda: disp.dispatch.await_count == 1)
    finally:
        await consumer.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert int((await redis.xpending(stream, group))["pending"]) == 0


@pytest.mark.asyncio
async def test_entry_that_never_succeeds_is_dead_lettered_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        trigger_bus_block_ms=20,
        trigger_bus_claim_idle_ms=0,
        trigger_bus_max_deliveries=2,
    )
    monkeypatch.setattr(bus, "get_settings", lambda: s)
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    store = _RecordingStore()  # stays down
    disp = AsyncMock()
    consumer = ChainTriggerConsumer(trigger_store=store, dispatcher=disp, redis=redis)
    await bus.publish_trigger_event(
        redis,
        "goal.completed",
        build_chain_event(channel="goal.completed", tenant_id="t1", goal_id="g-dead"),
    )
    stream, group = s.trigger_bus_stream_goal, ChainTriggerConsumer.GROUP
    dlq = bus.dead_letter_stream(stream)
    task = asyncio.create_task(consumer.start())

    async def _dead_lettered() -> int:
        return int(await redis.xlen(dlq))

    try:
        for _ in range(300):
            if await _dead_lettered():
                break
            await asyncio.sleep(0.01)
    finally:
        await consumer.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    entries = await redis.xrange(dlq)
    assert len(entries) == 1
    fields = entries[0][1]
    assert fields["group"] == group
    assert json.loads(fields["data"])["goal_id"] == "g-dead"
    assert int((await redis.xpending(stream, group))["pending"]) == 0
    disp.dispatch.assert_not_awaited()


def test_dead_letter_stream_name() -> None:
    assert bus.dead_letter_stream("trigger:stream:goal") == "trigger:stream:goal:dlq"
    assert inspect.iscoroutinefunction(bus.TriggerStreamReader._dead_letter)
