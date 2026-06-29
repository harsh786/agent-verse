"""Tests for civilization events — emit_event and get_events_since."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.civilization.events import CivEventType, emit_event, get_events_since


# ── helpers ───────────────────────────────────────────────────────────────────


class _noop_ctx:
    async def __aenter__(self) -> "_noop_ctx":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeSession:
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


# ── CivEventType constants ────────────────────────────────────────────────────


def test_civ_event_type_constants_exist():
    assert CivEventType.AGENT_SPAWNED == "agent_spawned"
    assert CivEventType.AGENT_RETIRED == "agent_retired"
    assert CivEventType.AGENT_UPDATED == "agent_updated"
    assert CivEventType.SPAWN_DENIED == "spawn_denied"
    assert CivEventType.GOAL_SUBMITTED == "goal_submitted"
    assert CivEventType.GOAL_COMPLETED == "goal_completed"
    assert CivEventType.DEBATE_STARTED == "debate_started"
    assert CivEventType.DEBATE_CONCLUDED == "debate_concluded"
    assert CivEventType.BLACKBOARD_POSTED == "blackboard_posted"
    assert CivEventType.LEARNING_CANDIDATE == "learning_candidate"
    assert CivEventType.LEARNING_PROMOTED == "learning_promoted"
    assert CivEventType.LEARNING_REJECTED == "learning_rejected"
    assert CivEventType.BREACH_DETECTED == "breach_detected"
    assert CivEventType.CIVILIZATION_PAUSED == "civilization_paused"
    assert CivEventType.CIVILIZATION_RESUMED == "civilization_resumed"
    assert CivEventType.BUS_MESSAGE == "bus_message"


# ── emit_event ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_event_returns_event_id_without_db_or_redis():
    event_id = await emit_event(
        civilization_id="civ-1",
        tenant_id="t1",
        event_type=CivEventType.AGENT_SPAWNED,
        payload={"agent_id": "a1"},
    )
    assert event_id
    assert isinstance(event_id, str)
    assert len(event_id) > 0


@pytest.mark.asyncio
async def test_emit_event_with_db_persists_row():
    session = _FakeSession()
    event_id = await emit_event(
        civilization_id="civ-1",
        tenant_id="t1",
        event_type=CivEventType.GOAL_SUBMITTED,
        payload={"goal": "do something"},
        db=lambda: session,
    )
    assert event_id
    assert len(session.executions) >= 1


@pytest.mark.asyncio
async def test_emit_event_with_db_exception_still_returns_id():
    """DB errors are swallowed; emit still returns an event_id."""

    class _FailSession:
        async def __aenter__(self) -> "_FailSession":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        def begin(self) -> "_FailBegin":
            return _FailBegin()

        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("DB down")

    class _FailBegin:
        async def __aenter__(self) -> None:
            raise RuntimeError("DB down")

        async def __aexit__(self, *args: Any) -> None:
            return None

    event_id = await emit_event(
        civilization_id="civ-1",
        tenant_id="t1",
        event_type=CivEventType.BREACH_DETECTED,
        payload={"reasons": ["budget"]},
        db=lambda: _FailSession(),
    )
    assert event_id  # still returns id despite DB failure


@pytest.mark.asyncio
async def test_emit_event_with_redis_publishes():
    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock()

    event_id = await emit_event(
        civilization_id="civ-1",
        tenant_id="t1",
        event_type=CivEventType.AGENT_SPAWNED,
        payload={"agent_id": "a1"},
        redis=mock_redis,
    )
    assert event_id
    mock_redis.publish.assert_called_once()
    call_args = mock_redis.publish.call_args
    channel = call_args[0][0]
    assert "civ_sse:t1:civ-1" == channel
    data = json.loads(call_args[0][1])
    assert data["id"] == event_id
    assert data["type"] == CivEventType.AGENT_SPAWNED


@pytest.mark.asyncio
async def test_emit_event_redis_exception_still_returns_id():
    """Redis publish errors are swallowed."""
    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock(side_effect=ConnectionError("Redis down"))

    event_id = await emit_event(
        civilization_id="civ-1",
        tenant_id="t1",
        event_type=CivEventType.BLACKBOARD_POSTED,
        payload={"entry_id": "e1"},
        redis=mock_redis,
    )
    assert event_id


@pytest.mark.asyncio
async def test_emit_event_with_db_and_redis_both_called():
    session = _FakeSession()
    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock()

    event_id = await emit_event(
        civilization_id="civ-1",
        tenant_id="t1",
        event_type=CivEventType.DEBATE_STARTED,
        payload={"debate_id": "d1"},
        db=lambda: session,
        redis=mock_redis,
    )
    assert event_id
    assert len(session.executions) >= 1
    mock_redis.publish.assert_called_once()


@pytest.mark.asyncio
async def test_emit_event_payload_is_json_serializable():
    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock()

    await emit_event(
        civilization_id="civ-1",
        tenant_id="t1",
        event_type=CivEventType.LEARNING_CANDIDATE,
        payload={"candidate_id": "c1", "score": 0.9, "tags": ["civ", "a1"]},
        redis=mock_redis,
    )
    call_data = json.loads(mock_redis.publish.call_args[0][1])
    assert call_data["payload"]["score"] == 0.9


@pytest.mark.asyncio
async def test_emit_event_full_event_has_required_fields():
    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock()

    await emit_event(
        civilization_id="civ-1",
        tenant_id="t1",
        event_type=CivEventType.CIVILIZATION_PAUSED,
        payload={"reason": "breach"},
        redis=mock_redis,
    )
    data = json.loads(mock_redis.publish.call_args[0][1])
    assert "id" in data
    assert "civilization_id" in data
    assert "tenant_id" in data
    assert "type" in data
    assert "payload" in data
    assert "ts" in data


# ── get_events_since ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_events_since_no_db_returns_empty():
    result = await get_events_since(
        civilization_id="civ-1",
        tenant_id="t1",
        db=None,
    )
    assert result == []


@pytest.mark.asyncio
async def test_get_events_since_returns_rows_from_db():
    now = datetime.now(UTC)
    rows = [("ev-1", "agent_spawned", {"agent_id": "a1"}, now)]
    session = _FakeSession(rows=rows)

    result = await get_events_since(
        civilization_id="civ-1",
        tenant_id="t1",
        db=lambda: session,
    )
    assert len(result) == 1
    assert result[0]["id"] == "ev-1"
    assert result[0]["type"] == "agent_spawned"
    assert result[0]["civilization_id"] == "civ-1"


@pytest.mark.asyncio
async def test_get_events_since_with_since_ts_filter():
    now = datetime.now(UTC)
    rows = [("ev-2", "goal_submitted", {"goal": "x"}, now)]
    session = _FakeSession(rows=rows)

    result = await get_events_since(
        civilization_id="civ-1",
        tenant_id="t1",
        since_ts=now,
        db=lambda: session,
    )
    assert len(result) == 1
    # Verify since_ts filter was included in query params
    _, params = session.executions[0]
    assert "since" in params


@pytest.mark.asyncio
async def test_get_events_since_with_event_types_filter():
    now = datetime.now(UTC)
    rows = [("ev-3", "agent_retired", {}, now)]
    session = _FakeSession(rows=rows)

    result = await get_events_since(
        civilization_id="civ-1",
        tenant_id="t1",
        event_types=["agent_retired"],
        db=lambda: session,
    )
    assert len(result) == 1
    # Verify types filter was included in query params
    _, params = session.executions[0]
    assert "types" in params


@pytest.mark.asyncio
async def test_get_events_since_with_both_filters():
    now = datetime.now(UTC)
    rows = []
    session = _FakeSession(rows=rows)

    result = await get_events_since(
        civilization_id="civ-1",
        tenant_id="t1",
        since_ts=now,
        event_types=["goal_submitted", "goal_completed"],
        limit=100,
        db=lambda: session,
    )
    assert result == []
    _, params = session.executions[0]
    assert "since" in params
    assert "types" in params


@pytest.mark.asyncio
async def test_get_events_since_payload_dict_passthrough():
    """Payload that is already a dict should pass through unchanged."""
    now = datetime.now(UTC)
    rows = [("ev-4", "blackboard_posted", {"entry_id": "e1", "confidence": 0.9}, now)]
    session = _FakeSession(rows=rows)

    result = await get_events_since(
        civilization_id="civ-1",
        tenant_id="t1",
        db=lambda: session,
    )
    assert result[0]["payload"] == {"entry_id": "e1", "confidence": 0.9}


@pytest.mark.asyncio
async def test_get_events_since_non_dict_payload_becomes_empty_dict():
    """Non-dict payload (e.g., string) returns {} to match spec."""
    now = datetime.now(UTC)
    rows = [("ev-5", "bus_message", "not-a-dict", now)]
    session = _FakeSession(rows=rows)

    result = await get_events_since(
        civilization_id="civ-1",
        tenant_id="t1",
        db=lambda: session,
    )
    assert result[0]["payload"] == {}


@pytest.mark.asyncio
async def test_get_events_since_none_ts_becomes_empty_string():
    """Row with None ts produces empty ts string."""
    rows = [("ev-6", "agent_updated", {}, None)]
    session = _FakeSession(rows=rows)

    result = await get_events_since(
        civilization_id="civ-1",
        tenant_id="t1",
        db=lambda: session,
    )
    assert result[0]["ts"] == ""


@pytest.mark.asyncio
async def test_get_events_since_db_exception_returns_empty():
    """DB errors are swallowed and empty list is returned."""

    class _ExplodingSession:
        async def __aenter__(self) -> "_ExplodingSession":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("DB exploded")

    result = await get_events_since(
        civilization_id="civ-1",
        tenant_id="t1",
        db=lambda: _ExplodingSession(),
    )
    assert result == []


@pytest.mark.asyncio
async def test_get_events_since_respects_limit_param():
    now = datetime.now(UTC)
    session = _FakeSession(rows=[])

    await get_events_since(
        civilization_id="civ-1",
        tenant_id="t1",
        limit=42,
        db=lambda: session,
    )
    _, params = session.executions[0]
    assert params["limit"] == 42
