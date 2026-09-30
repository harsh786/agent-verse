"""TRG-17: one Redis error no longer kills an event-driven trigger consumer for good.

Each consumer's read loop (then ``pubsub.listen()``) logged and returned on the first error
and the supervisor never restarted it, so a Redis failover silently disabled
chain / HITL / memory / event / condition / chat triggers until the pod
restarted. The supervisor now restarts an exited consumer with exponential
backoff and reports per-consumer health (surfaced in /health).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import fakeredis
import pytest

from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.supervisor import TriggerConsumerSupervisor
from tests.triggers.stream_support import publish


class _FlakyRedis(fakeredis.FakeAsyncRedis):
    """The first XREADGROUP fails (Redis failover); later reads work.

    TRG-18: consumers read their trigger stream through a consumer group; the
    event published before the failover stays in the stream meanwhile.
    """

    def __init__(self) -> None:
        super().__init__(decode_responses=True)
        self.subscriptions = 0  # consumer (re)starts: one group join each

    async def xgroup_create(self, *args: Any, **kwargs: Any) -> Any:
        self.subscriptions += 1
        return await super().xgroup_create(*args, **kwargs)

    async def xreadgroup(self, *args: Any, **kwargs: Any) -> Any:
        if self.subscriptions == 1:
            raise ConnectionError("Connection closed by server.")
        return await super().xreadgroup(*args, **kwargs)


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
    await publish(redis, "trigger:event:deploys", {"tenant_id": "t1", "event_id": "e1"})
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
