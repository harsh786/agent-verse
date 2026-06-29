"""Unit tests for CivilizationBus."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.civilization.bus import CivilizationBus, _nullctx


# ── helpers ───────────────────────────────────────────────────────────────────


class _noop_ctx:
    """Null async context manager."""

    async def __aenter__(self) -> "_noop_ctx":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeSession:
    """Minimal async session mock for DB path tests."""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.executions: list[Any] = []
        self._rows = rows or []

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def begin(self) -> _noop_ctx:
        return _noop_ctx()

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.executions.append((statement, params))
        return SimpleNamespace(
            fetchall=lambda: list(self._rows),
            fetchone=lambda: self._rows[0] if self._rows else None,
        )


def _make_bus(db=None, redis=None) -> CivilizationBus:
    return CivilizationBus(
        civilization_id="civ-1",
        tenant_id="t1",
        db_session_factory=db,
        redis=redis,
    )


# ── channel format tests ─────────────────────────────────────────────────────


def test_channel_format():
    bus = _make_bus()
    assert bus._channel("spawn") == "civ:t1:civ-1:spawn"
    assert bus._channel("findings") == "civ:t1:civ-1:findings"


def test_channel_format_all_valid_topics():
    bus = _make_bus()
    for topic in ("spawn", "findings", "debate", "coordination", "lifecycle", "system"):
        ch = bus._channel(topic)
        assert ch == f"civ:t1:civ-1:{topic}"


# ── publish tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_persists_to_db():
    session = _FakeSession()
    bus = _make_bus(db=lambda: session)
    msg_id = await bus.publish(
        from_agent_id="agent-1",
        topic="findings",
        payload={"result": "found 5 issues"},
    )

    assert msg_id  # returns a non-empty ID
    # _persist_message + _emit_civilization_event → 2 executes
    assert len(session.executions) >= 1


@pytest.mark.asyncio
async def test_publish_sends_to_redis():
    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock()

    bus = _make_bus(redis=mock_redis)
    await bus.publish(
        from_agent_id="agent-1",
        topic="spawn",
        payload={"spawned_agent_id": "a2"},
    )

    mock_redis.publish.assert_called_once()
    channel_arg = mock_redis.publish.call_args[0][0]
    assert "spawn" in channel_arg
    assert "civ-1" in channel_arg


@pytest.mark.asyncio
async def test_publish_normalizes_invalid_topic():
    bus = _make_bus()
    # Should not raise; invalid topic → "system"
    msg_id = await bus.publish(
        from_agent_id="agent-1",
        topic="invalid_topic_xyz",
        payload={"data": "test"},
    )
    assert msg_id


@pytest.mark.asyncio
async def test_publish_invalid_topic_uses_system_channel():
    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock()

    bus = _make_bus(redis=mock_redis)
    await bus.publish(
        from_agent_id="agent-1",
        topic="__not_valid__",
        payload={"x": 1},
    )

    mock_redis.publish.assert_called_once()
    channel_arg = mock_redis.publish.call_args[0][0]
    # invalid topic is normalised to "system"
    assert "system" in channel_arg


@pytest.mark.asyncio
async def test_publish_returns_nonempty_message_id():
    bus = _make_bus()
    msg_id = await bus.publish(
        from_agent_id="a1",
        topic="lifecycle",
        payload={},
    )
    assert isinstance(msg_id, str)
    assert len(msg_id) > 0


@pytest.mark.asyncio
async def test_publish_no_redis_still_returns_id():
    bus = _make_bus(redis=None)
    msg_id = await bus.publish(
        from_agent_id="a1",
        topic="coordination",
        payload={"action": "sync"},
    )
    assert msg_id


# ── get_messages tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_messages_returns_from_db():
    now = datetime.now(UTC)
    rows = [("msg-1", "agent-1", "findings", {"result": "found"}, now)]
    session = _FakeSession(rows=rows)

    bus = _make_bus(db=lambda: session)
    messages = await bus.get_messages(limit=10)

    assert len(messages) == 1
    assert messages[0]["id"] == "msg-1"
    assert messages[0]["topic"] == "findings"


@pytest.mark.asyncio
async def test_get_messages_empty_when_no_db():
    bus = _make_bus()
    messages = await bus.get_messages(limit=10)
    assert messages == []


@pytest.mark.asyncio
async def test_get_messages_with_topic_filter():
    now = datetime.now(UTC)
    rows = [("msg-2", "agent-2", "debate", {"pos": "yes"}, now)]
    session = _FakeSession(rows=rows)

    bus = _make_bus(db=lambda: session)
    messages = await bus.get_messages(topic="debate", limit=5)

    assert len(messages) == 1
    assert messages[0]["topic"] == "debate"


@pytest.mark.asyncio
async def test_get_messages_civilization_id_in_result():
    now = datetime.now(UTC)
    rows = [("msg-3", "agent-3", "spawn", {}, now)]
    session = _FakeSession(rows=rows)

    bus = _make_bus(db=lambda: session)
    messages = await bus.get_messages()

    assert messages[0]["civilization_id"] == "civ-1"


@pytest.mark.asyncio
async def test_publish_db_and_redis_both_called():
    session = _FakeSession()
    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock()

    bus = _make_bus(db=lambda: session, redis=mock_redis)
    msg_id = await bus.publish(
        from_agent_id="a1",
        topic="findings",
        payload={"data": "x"},
    )

    assert msg_id
    assert len(session.executions) >= 1
    mock_redis.publish.assert_called_once()


# ── _all_channel ──────────────────────────────────────────────────────────────


def test_all_channel_format():
    bus = _make_bus()
    ch = bus._all_channel()
    assert ch == "civ:t1:civ-1:*"


# ── get_messages with filters ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_messages_with_from_agent_id_filter():
    now = datetime.now(UTC)
    rows = [("msg-10", "agent-x", "coordination", {}, now)]
    session = _FakeSession(rows=rows)

    bus = _make_bus(db=lambda: session)
    messages = await bus.get_messages(from_agent_id="agent-x", limit=5)

    assert len(messages) == 1
    _, params = session.executions[0]
    assert "from_agent" in params
    assert params["from_agent"] == "agent-x"


@pytest.mark.asyncio
async def test_get_messages_with_since_ts_filter():
    now = datetime.now(UTC)
    rows = [("msg-11", "agent-y", "lifecycle", {}, now)]
    session = _FakeSession(rows=rows)

    bus = _make_bus(db=lambda: session)
    messages = await bus.get_messages(since_ts=now, limit=5)

    assert len(messages) == 1
    _, params = session.executions[0]
    assert "since" in params


@pytest.mark.asyncio
async def test_get_messages_with_all_filters():
    now = datetime.now(UTC)
    rows = []
    session = _FakeSession(rows=rows)

    bus = _make_bus(db=lambda: session)
    await bus.get_messages(topic="spawn", from_agent_id="a1", since_ts=now, limit=3)

    _, params = session.executions[0]
    assert params["topic"] == "spawn"
    assert params["from_agent"] == "a1"
    assert "since" in params


@pytest.mark.asyncio
async def test_get_messages_db_exception_returns_empty():
    class _FailSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def execute(self, *args, **kwargs):
            raise RuntimeError("DB down")

    bus = _make_bus(db=lambda: _FailSession())
    messages = await bus.get_messages(limit=5)
    assert messages == []


@pytest.mark.asyncio
async def test_get_messages_payload_non_dict_becomes_empty_dict():
    now = datetime.now(UTC)
    rows = [("msg-12", "agent-z", "findings", "not-a-dict", now)]
    session = _FakeSession(rows=rows)

    bus = _make_bus(db=lambda: session)
    messages = await bus.get_messages()
    assert messages[0]["payload"] == {}


@pytest.mark.asyncio
async def test_get_messages_none_ts_becomes_empty_string():
    rows = [("msg-13", "agent-z", "findings", {}, None)]
    session = _FakeSession(rows=rows)

    bus = _make_bus(db=lambda: session)
    messages = await bus.get_messages()
    assert messages[0]["ts"] == ""


# ── Redis publish exception path ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_redis_exception_is_swallowed():
    """Redis publish errors must not propagate out of publish()."""
    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock(side_effect=ConnectionError("Redis down"))

    bus = _make_bus(redis=mock_redis)
    msg_id = await bus.publish(
        from_agent_id="a1",
        topic="spawn",
        payload={"test": True},
    )
    assert msg_id  # publish still returns a message_id


# ── _persist_message exception ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_message_db_exception_is_swallowed():
    class _FailSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def begin(self):
            class _FailCtx:
                async def __aenter__(self):
                    raise RuntimeError("DB insert failed")

                async def __aexit__(self, *_):
                    return None

            return _FailCtx()

        async def execute(self, *args, **kwargs):
            raise RuntimeError("DB insert failed")

    bus = _make_bus(db=lambda: _FailSession())
    # Should not raise
    msg_id = await bus.publish(
        from_agent_id="a1",
        topic="lifecycle",
        payload={"event": "test"},
    )
    assert msg_id


# ── _emit_civilization_event exception ───────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_civilization_event_db_exception_is_swallowed():
    call_count = 0

    class _PartialFailSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def begin(self):
            return _noop_ctx()

        async def execute(self, statement, params=None):
            nonlocal call_count
            call_count += 1
            # First call (_persist_message) succeeds, second (_emit_civ_event) fails
            if call_count >= 2:
                raise RuntimeError("Event table missing")
            from types import SimpleNamespace as NS
            return NS(fetchall=lambda: [], fetchone=lambda: None)

    bus = _make_bus(db=lambda: _PartialFailSession())
    msg_id = await bus.publish(
        from_agent_id="a1",
        topic="coordination",
        payload={"x": 1},
    )
    assert msg_id


# ── _nullctx ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nullctx_as_context_manager():
    from app.civilization.bus import _nullctx

    value = object()
    async with _nullctx(value) as v:
        assert v is value


@pytest.mark.asyncio
async def test_nullctx_exit_returns_none():
    from app.civilization.bus import _nullctx

    ctx = _nullctx("test-value")
    result = await ctx.__aenter__()
    assert result == "test-value"
    exit_result = await ctx.__aexit__(None, None, None)
    assert exit_result is None


# ── subscribe: no-redis path ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subscribe_no_redis_yields_nothing():
    bus = _make_bus(redis=None)
    results = []
    async for msg in bus.subscribe(topics=["spawn"]):
        results.append(msg)
    assert results == []


@pytest.mark.asyncio
async def test_subscribe_with_redis_yields_messages():
    """Subscribe iterates Redis pub/sub messages and yields valid JSON payloads."""
    collected = []

    class _MockListen:
        def __init__(self, msgs):
            self._msgs = msgs
            self._idx = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._idx >= len(self._msgs):
                raise StopAsyncIteration
            msg = self._msgs[self._idx]
            self._idx += 1
            return msg

    class _MockPubSub:
        def __init__(self, messages):
            self._messages = messages

        async def subscribe(self, *channels):
            pass

        async def unsubscribe(self, *channels):
            pass

        def listen(self):
            return _MockListen(self._messages)

    class _MockRedis:
        def __init__(self, messages):
            self._pubsub = _MockPubSub(messages)

        def pubsub(self):
            return self._pubsub

    messages = [
        {"type": "message", "data": json.dumps({"id": "m1", "topic": "spawn"})},
        {"type": "subscribe", "data": "ignored-sub-confirm"},
        {"type": "message", "data": json.dumps({"id": "m2", "topic": "findings"})},
    ]
    mock_redis = _MockRedis(messages)
    bus = _make_bus(redis=mock_redis)

    async for msg in bus.subscribe(topics=["spawn", "findings"]):
        collected.append(msg)

    assert len(collected) == 2
    assert collected[0]["id"] == "m1"
    assert collected[1]["id"] == "m2"


@pytest.mark.asyncio
async def test_subscribe_with_invalid_json_is_skipped():
    """Invalid JSON messages are silently ignored."""
    collected = []

    class _MockListen:
        def __init__(self):
            self._msgs = [
                {"type": "message", "data": "NOT VALID JSON {{{{"},
                {"type": "message", "data": json.dumps({"id": "m-good"})},
            ]
            self._idx = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._idx >= len(self._msgs):
                raise StopAsyncIteration
            m = self._msgs[self._idx]
            self._idx += 1
            return m

    class _MockPubSub:
        async def subscribe(self, *_):
            pass

        async def unsubscribe(self, *_):
            pass

        def listen(self):
            return _MockListen()

    class _MockRedis:
        def pubsub(self):
            return _MockPubSub()

    bus = _make_bus(redis=_MockRedis())
    async for msg in bus.subscribe(topics=["spawn"]):
        collected.append(msg)

    assert len(collected) == 1
    assert collected[0]["id"] == "m-good"


@pytest.mark.asyncio
async def test_subscribe_no_topics_uses_all_valid():
    """subscribe() with no topics argument subscribes to all valid topics."""
    from app.civilization.bus import _VALID_TOPICS
    collected_channels = []

    class _MockPubSub:
        async def subscribe(self, *channels):
            collected_channels.extend(channels)

        async def unsubscribe(self, *channels):
            pass

        def listen(self):
            class _Empty:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    raise StopAsyncIteration

            return _Empty()

    class _MockRedis:
        def pubsub(self):
            return _MockPubSub()

    bus = _make_bus(redis=_MockRedis())
    async for _ in bus.subscribe():
        pass

    # Should have subscribed to all valid topics
    assert len(collected_channels) == len(_VALID_TOPICS)
