"""TRG-18: the shared trigger-bus publish helper.

Every trigger-event publisher XADDs to a capped Redis Stream per event family
(so an event published while consumers are down is still there when they come
back) and — while ``trigger_bus_dual_publish`` is on — also PUBLISHes to the
legacy pub/sub channel so replicas still running the pub/sub consumers during a
rolling deploy keep receiving events.
"""

from __future__ import annotations

import json
from typing import Any

import fakeredis
import pytest

from app.core.config import Settings
from app.triggers import bus


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    s = _settings()
    monkeypatch.setattr(bus, "get_settings", lambda: s)
    return s


class _Recorder:
    """Async client recording XADD / PUBLISH calls (optionally failing XADD)."""

    def __init__(self, *, fail_xadd: bool = False, fail_publish: bool = False) -> None:
        self.xadds: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        self.publishes: list[tuple[str, str]] = []
        self._fail_xadd = fail_xadd
        self._fail_publish = fail_publish

    async def xadd(self, name: str, fields: dict[str, str], **kwargs: Any) -> str:
        if self._fail_xadd:
            raise ConnectionError("redis down")
        self.xadds.append((name, fields, kwargs))
        return "1-0"

    async def publish(self, channel: str, message: str) -> int:
        if self._fail_publish:
            raise ConnectionError("redis down")
        self.publishes.append((channel, message))
        return 1


def test_settings_defaults() -> None:
    s = _settings()
    assert s.trigger_bus_dual_publish is True
    assert s.trigger_bus_stream_maxlen == 100_000
    assert s.trigger_bus_stream_goal and s.trigger_bus_stream_hitl
    assert s.trigger_bus_stream_memory and s.trigger_bus_stream_event


def test_dual_publish_env_var_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRIGGER_BUS_DUAL_PUBLISH", "false")
    assert _settings().trigger_bus_dual_publish is False


@pytest.mark.parametrize(
    ("channel", "attr"),
    [
        ("goal.completed", "trigger_bus_stream_goal"),
        ("goal.failed", "trigger_bus_stream_goal"),
        ("goal.score_below", "trigger_bus_stream_goal"),
        ("hitl.approved", "trigger_bus_stream_hitl"),
        ("hitl.rejected", "trigger_bus_stream_hitl"),
        ("memory.created", "trigger_bus_stream_memory"),
        ("trigger:event:orders", "trigger_bus_stream_event"),
        ("trigger:event:conversational", "trigger_bus_stream_event"),
        ("trigger:event:state_machine.transition", "trigger_bus_stream_event"),
    ],
)
def test_stream_for_channel_maps_each_family(settings: Settings, channel: str, attr: str) -> None:
    assert bus.stream_for_channel(channel) == getattr(settings, attr)


def test_unknown_channel_is_rejected(settings: Settings) -> None:
    with pytest.raises(ValueError, match="not a trigger-bus channel"):
        bus.stream_for_channel("platform_events:t1")


async def test_xadd_with_approximate_maxlen_and_dual_publish(settings: Settings) -> None:
    redis = _Recorder()
    payload = {"tenant_id": "t1", "goal_id": "g1"}

    entry_id = await bus.publish_trigger_event(redis, "goal.completed", payload)

    assert entry_id == "1-0"
    [(stream, fields, kwargs)] = redis.xadds
    assert stream == settings.trigger_bus_stream_goal
    assert fields["channel"] == "goal.completed"
    assert json.loads(fields["data"]) == payload
    assert kwargs == {"maxlen": 100_000, "approximate": True}
    assert redis.publishes == [("goal.completed", fields["data"])]


async def test_dual_publish_off_skips_pubsub(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(trigger_bus_dual_publish=False, trigger_bus_stream_maxlen=500)
    monkeypatch.setattr(bus, "get_settings", lambda: s)
    redis = _Recorder()

    await bus.publish_trigger_event(redis, "memory.created", {"tenant_id": "t1"})

    assert redis.publishes == []
    assert redis.xadds[0][2] == {"maxlen": 500, "approximate": True}


async def test_payload_dict_is_passed_through_untouched(settings: Settings) -> None:
    redis = _Recorder()
    payload = {"tenant_id": "t1", "request_id": "r1", "hitl_queue_id": "q-7", "extra": [1, 2]}

    await bus.publish_trigger_event(redis, "hitl.approved", payload)

    assert json.loads(redis.xadds[0][1]["data"]) == payload
    assert payload == {
        "tenant_id": "t1",
        "request_id": "r1",
        "hitl_queue_id": "q-7",
        "extra": [1, 2],
    }


async def test_prebuilt_json_string_payload_is_sent_verbatim(settings: Settings) -> None:
    redis = _Recorder()
    raw = json.dumps({"tenant_id": "t1", "goal_id": "g1"})

    await bus.publish_trigger_event(redis, "goal.failed", raw)

    assert redis.xadds[0][1]["data"] == raw
    assert redis.publishes == [("goal.failed", raw)]


async def test_xadd_failure_still_dual_publishes_then_raises(settings: Settings) -> None:
    """The stream is the durable path: its failure is reported (never a fake
    success), but replicas still on pub/sub get the event."""
    redis = _Recorder(fail_xadd=True)

    with pytest.raises(bus.TriggerBusPublishError):
        await bus.publish_trigger_event(redis, "goal.completed", {"tenant_id": "t1"})

    assert [c for c, _ in redis.publishes] == ["goal.completed"]


async def test_pubsub_failure_after_xadd_does_not_raise(settings: Settings) -> None:
    redis = _Recorder(fail_publish=True)

    entry_id = await bus.publish_trigger_event(redis, "goal.completed", {"tenant_id": "t1"})

    assert entry_id == "1-0"
    assert len(redis.xadds) == 1


async def test_real_stream_entry_and_pubsub_message(settings: Settings) -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    pubsub = redis.pubsub()
    await pubsub.subscribe("trigger:event:orders")
    await pubsub.get_message(timeout=0.1)  # subscribe confirmation

    await bus.publish_trigger_event(redis, "trigger:event:orders", {"tenant_id": "t1", "n": 1})

    entries = await redis.xrange(settings.trigger_bus_stream_event)
    assert len(entries) == 1
    fields = entries[0][1]
    assert fields["channel"] == "trigger:event:orders"
    assert json.loads(fields["data"]) == {"tenant_id": "t1", "n": 1}
    message = await pubsub.get_message(timeout=0.5)
    assert message is not None and json.loads(message["data"]) == {"tenant_id": "t1", "n": 1}
    await pubsub.aclose()


def test_sync_variant_for_worker_clients(settings: Settings) -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)

    entry_id = bus.publish_trigger_event_sync(redis, "goal.score_below", {"tenant_id": "t1"})

    entries = redis.xrange(settings.trigger_bus_stream_goal)
    assert [e[0] for e in entries] == [entry_id]
    assert entries[0][1]["channel"] == "goal.score_below"


async def test_async_helper_accepts_sync_client(settings: Settings) -> None:
    """run_goal's async event callback holds a *sync* worker Redis client."""
    redis = fakeredis.FakeRedis(decode_responses=True)

    await bus.publish_trigger_event(redis, "goal.completed", {"tenant_id": "t1"})

    assert len(redis.xrange(settings.trigger_bus_stream_goal)) == 1
