"""2.W-1: the EVENT trigger consumer fires EVENT triggers on published events,
scoped to the event's tenant."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.triggers.consumers.event import (
    EventTriggerConsumer,
    event_channel_name,
    publish_trigger_event,
)
from tests.triggers.stream_support import drain, publish, stream_redis

pytestmark = pytest.mark.asyncio


class _FakeRedis:
    """Publisher-side fake (sync client) recording PUBLISH and XADD."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self.streamed: list[tuple[str, dict[str, str]]] = []

    def publish(self, channel: str, data: str) -> None:  # sync
        self.published.append((channel, data))

    def xadd(self, stream: str, fields: dict[str, str], **_: Any) -> str:  # sync
        self.streamed.append((stream, fields))
        return f"{len(self.streamed)}-0"


class _FakeStore:
    def __init__(self, triggers_by_tenant: dict[str, list[dict[str, Any]]]) -> None:
        self._t = triggers_by_tenant

    async def find_by_type_async(
        self, ttype: str, *, tenant_id: str, strict: bool = False
    ) -> list[dict[str, Any]]:
        assert ttype == "event"
        return self._t.get(tenant_id, [])


class _FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def dispatch(
        self, spec: Any, payload: Any, tenant_ctx: Any, *, message_id: str = ""
    ) -> None:
        self.calls.append((spec, payload, tenant_ctx, message_id))


async def _stream_with(event_channel: str, body: dict[str, Any]) -> Any:
    """A fake Redis whose EVENT stream already holds one published event."""
    redis = stream_redis()
    await publish(redis, event_channel_name(event_channel), body)
    return redis


def _trigger(event_channel: str) -> dict[str, Any]:
    return {"spec": SimpleNamespace(event_channel=event_channel)}


async def test_event_fires_matching_trigger() -> None:
    redis = await _stream_with("deployments", {"tenant_id": "t1", "event_id": "e1"})
    store = _FakeStore({"t1": [_trigger("deployments")]})
    disp = _FakeDispatcher()
    await drain(EventTriggerConsumer(trigger_store=store, dispatcher=disp, redis=redis))
    assert len(disp.calls) == 1
    assert disp.calls[0][3] == "e1"  # message_id threaded for idempotency


async def test_event_non_matching_channel_does_not_fire() -> None:
    redis = await _stream_with("deployments", {"tenant_id": "t1"})
    store = _FakeStore({"t1": [_trigger("some-other-channel")]})
    disp = _FakeDispatcher()
    await drain(EventTriggerConsumer(trigger_store=store, dispatcher=disp, redis=redis))
    assert disp.calls == []


async def test_event_is_tenant_scoped() -> None:
    # Event carries tenant t1; tenant t2 also has a matching-channel trigger but
    # must NOT fire (find_by_type_async is scoped to the event's tenant).
    redis = await _stream_with("deployments", {"tenant_id": "t1"})
    store = _FakeStore({"t1": [], "t2": [_trigger("deployments")]})
    disp = _FakeDispatcher()
    await drain(EventTriggerConsumer(trigger_store=store, dispatcher=disp, redis=redis))
    assert disp.calls == []


async def test_event_without_tenant_id_is_ignored() -> None:
    redis = await _stream_with("deployments", {"no_tenant": True})
    store = _FakeStore({"t1": [_trigger("deployments")]})
    disp = _FakeDispatcher()
    await drain(EventTriggerConsumer(trigger_store=store, dispatcher=disp, redis=redis))
    assert disp.calls == []


async def test_no_redis_disables_consumer() -> None:
    disp = _FakeDispatcher()
    # Should not raise when redis is absent.
    await EventTriggerConsumer(trigger_store=_FakeStore({}), dispatcher=disp, redis=None).start()
    assert disp.calls == []


async def test_publish_trigger_event_stamps_tenant_and_channel() -> None:
    redis = _FakeRedis()
    await publish_trigger_event(
        redis, event_channel="deployments", tenant_id="t1", payload={"sha": "abc"}
    )
    assert len(redis.published) == 1
    channel, data = redis.published[0]
    assert channel == "trigger:event:deployments"
    body = json.loads(data)
    assert body["tenant_id"] == "t1"
    assert body["sha"] == "abc"
