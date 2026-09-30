"""TRG-17: one Redis error no longer kills an event-driven trigger consumer for good.

Each consumer's ``pubsub.listen()`` loop logged and returned on the first error
and the supervisor never restarted it, so a Redis failover silently disabled
chain / HITL / memory / event / condition / chat triggers until the pod
restarted. The supervisor now restarts an exited consumer with exponential
backoff and reports per-consumer health (surfaced in /health).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.supervisor import TriggerConsumerSupervisor


class _FlakyPubSub:
    """First listen() raises (Redis failover); later ones deliver one event."""

    def __init__(self, redis: _FlakyRedis) -> None:
        self._redis = redis

    async def psubscribe(self, *_: str) -> None:
        self._redis.subscriptions += 1

    async def subscribe(self, *_: str) -> None:
        self._redis.subscriptions += 1

    async def listen(self) -> Any:
        if self._redis.subscriptions == 1:
            raise ConnectionError("Connection closed by server.")
        yield {
            "type": "pmessage",
            "channel": b"trigger:event:deploys",
            "data": json.dumps({"tenant_id": "t1", "event_id": "e1"}).encode(),
        }
        while True:
            await asyncio.sleep(3600)


class _FlakyRedis:
    def __init__(self) -> None:
        self.subscriptions = 0

    def pubsub(self) -> _FlakyPubSub:
        return _FlakyPubSub(self)


class _Store:
    async def find_by_type_async(self, trigger_type: str, *, tenant_id: str, **_: Any) -> list:
        if trigger_type == "event":
            return [{"spec": TriggerSpec(trigger_type=TriggerType.EVENT, event_channel="deploys")}]
        return []


async def test_consumer_resubscribes_after_listen_raises_and_handles_next_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _FlakyRedis()
    dispatcher = AsyncMock()
    dispatcher.resolve_tenant_plan = AsyncMock(return_value="free")
    sup = TriggerConsumerSupervisor(
        schedule_store=_Store(), dispatcher=dispatcher, redis=redis,
        restart_backoff_s=0.01, restart_backoff_max_s=0.02,
    )
    # Only the EVENT consumer, to keep the scenario focused.
    monkeypatch.setattr(sup, "_consumer_specs", lambda: [
        s for s in TriggerConsumerSupervisor._consumer_specs(sup) if s.name == "EventTriggerConsumer"
    ])
    await sup.start()
    try:
        for _ in range(200):
            if dispatcher.dispatch.await_count:
                break
            await asyncio.sleep(0.01)
        assert redis.subscriptions >= 2, "consumer was not restarted after the error"
        dispatcher.dispatch.assert_awaited()
        health = sup.health()
        assert health["EventTriggerConsumer"]["restarts"] >= 1
        assert health["EventTriggerConsumer"]["state"] == "running"
    finally:
        await sup.stop()


async def test_health_check_raises_while_a_consumer_is_down() -> None:
    sup = TriggerConsumerSupervisor()
    sup._health = {"ChainTriggerConsumer": {"state": "restarting", "restarts": 3}}
    with pytest.raises(RuntimeError, match="ChainTriggerConsumer"):
        await sup.check_health()
    sup._health = {"ChainTriggerConsumer": {"state": "running", "restarts": 3}}
    await sup.check_health()


async def test_stop_does_not_trigger_a_restart() -> None:
    class _Once:
        def __init__(self) -> None:
            self.starts = 0

        async def start(self) -> None:
            self.starts += 1
            await asyncio.sleep(3600)

        async def stop(self) -> None:
            return None

    sup = TriggerConsumerSupervisor(restart_backoff_s=0.0)
    consumer = _Once()
    sup._started = True
    task = asyncio.create_task(sup._run_consumer("X", consumer))  # type: ignore[arg-type]
    sup.tasks.append(task)
    sup.consumers.append(consumer)  # type: ignore[arg-type]
    await asyncio.sleep(0.01)
    await sup.stop()
    assert consumer.starts == 1
    assert sup.health()["X"]["state"] == "stopped"
