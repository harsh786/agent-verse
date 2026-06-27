"""Unit tests for CivilizationBus."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.civilization.bus import CivilizationBus


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
