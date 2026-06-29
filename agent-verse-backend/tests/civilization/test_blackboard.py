"""Unit tests for Blackboard (optimistic concurrency + conflict detection)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from app.civilization.blackboard import Blackboard, BlackboardConflictError


def _make_board(**kwargs) -> Blackboard:
    return Blackboard(
        civilization_id="civ-1",
        tenant_id="t1",
        db_session_factory=kwargs.get("db"),
        bus=kwargs.get("bus"),
    )


@pytest.mark.asyncio
async def test_post_adds_entry_in_memory():
    board = _make_board()
    entry = await board.post(
        author_agent_id="a1",
        topic="jira_issues",
        content="Found 15 open P1 issues",
        confidence=0.9,
    )
    assert entry["topic"] == "jira_issues"
    assert entry["confidence"] == 0.9
    assert entry["version"] == 1


@pytest.mark.asyncio
async def test_post_returns_full_entry_fields():
    board = _make_board()
    entry = await board.post(
        author_agent_id="a1",
        topic="bugs",
        content="3 critical bugs",
        confidence=0.85,
        refs=["bug-1", "bug-2"],
    )
    assert entry["id"]
    assert entry["author_agent_id"] == "a1"
    assert entry["refs"] == ["bug-1", "bug-2"]
    assert "created_at" in entry


@pytest.mark.asyncio
async def test_query_returns_posted_entry():
    board = _make_board()
    await board.post(
        author_agent_id="a1", topic="refunds", content="3 refunds pending", confidence=0.8
    )
    results = await board.query(topic="refunds")
    assert len(results) == 1
    assert "refunds" in results[0]["content"]


@pytest.mark.asyncio
async def test_query_filters_by_min_confidence():
    board = _make_board()
    await board.post(
        author_agent_id="a1", topic="issues", content="high conf", confidence=0.9
    )
    await board.post(
        author_agent_id="a2", topic="issues", content="low conf", confidence=0.3
    )
    results = await board.query(topic="issues", min_confidence=0.7)
    assert len(results) == 1
    assert "high conf" in results[0]["content"]


@pytest.mark.asyncio
async def test_query_returns_all_above_min_confidence():
    board = _make_board()
    await board.post(author_agent_id="a1", topic="t", content="c1", confidence=0.8)
    await board.post(author_agent_id="a2", topic="t", content="c2", confidence=0.9)
    await board.post(author_agent_id="a3", topic="t", content="c3", confidence=0.5)
    results = await board.query(topic="t", min_confidence=0.7)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_query_filters_by_author():
    board = _make_board()
    await board.post(author_agent_id="a1", topic="x", content="from a1", confidence=0.8)
    await board.post(author_agent_id="a2", topic="x", content="from a2", confidence=0.8)
    results = await board.query(author_agent_id="a1")
    assert all(r["author_agent_id"] == "a1" for r in results)


@pytest.mark.asyncio
async def test_query_empty_when_no_entries():
    board = _make_board()
    results = await board.query(topic="nonexistent")
    assert results == []


@pytest.mark.asyncio
async def test_update_with_correct_version():
    board = _make_board()
    entry = await board.post(
        author_agent_id="a1", topic="t", content="original", confidence=0.8
    )
    updated = await board.update(
        entry_id=entry["id"],
        author_agent_id="a1",
        content="updated",
        confidence=0.9,
        expected_version=1,
    )
    assert updated["version"] == 2


@pytest.mark.asyncio
async def test_update_increments_version():
    board = _make_board()
    entry = await board.post(
        author_agent_id="a1", topic="t", content="v1", confidence=0.8
    )
    updated = await board.update(
        entry_id=entry["id"],
        author_agent_id="a1",
        content="v2",
        confidence=0.85,
        expected_version=1,
    )
    # Now update again
    updated2 = await board.update(
        entry_id=entry["id"],
        author_agent_id="a1",
        content="v3",
        confidence=0.9,
        expected_version=2,
    )
    assert updated2["version"] == 3


@pytest.mark.asyncio
async def test_update_with_wrong_version_raises_conflict():
    board = _make_board()
    entry = await board.post(
        author_agent_id="a1", topic="t", content="original", confidence=0.8
    )
    with pytest.raises(BlackboardConflictError) as exc_info:
        await board.update(
            entry_id=entry["id"],
            author_agent_id="a1",
            content="updated",
            confidence=0.9,
            expected_version=999,  # wrong version
        )
    assert exc_info.value.expected_version == 999


@pytest.mark.asyncio
async def test_update_conflict_error_carries_current_version():
    board = _make_board()
    entry = await board.post(
        author_agent_id="a1", topic="t", content="original", confidence=0.8
    )
    with pytest.raises(BlackboardConflictError) as exc_info:
        await board.update(
            entry_id=entry["id"],
            author_agent_id="a1",
            content="stale",
            confidence=0.9,
            expected_version=5,
        )
    # current version should be 1 (from the post)
    assert exc_info.value.current_version == 1


@pytest.mark.asyncio
async def test_conflict_triggers_debate_via_bus():
    mock_bus = AsyncMock()
    mock_bus.publish = AsyncMock()

    board = _make_board(bus=mock_bus)
    # Post first high-confidence claim
    await board.post(
        author_agent_id="a1",
        topic="performance",
        content="system slow",
        confidence=0.8,
    )
    # Post conflicting high-confidence claim from different agent
    await board.post(
        author_agent_id="a2",
        topic="performance",
        content="system fast",
        confidence=0.9,
    )

    # Should have triggered debate
    debate_calls = [c for c in mock_bus.publish.call_args_list if "debate" in str(c)]
    assert len(debate_calls) >= 1


@pytest.mark.asyncio
async def test_low_confidence_no_debate_trigger():
    mock_bus = AsyncMock()
    mock_bus.publish = AsyncMock()

    board = _make_board(bus=mock_bus)
    # Both below threshold — no debate
    await board.post(
        author_agent_id="a1", topic="perf", content="slow", confidence=0.5
    )
    await board.post(
        author_agent_id="a2", topic="perf", content="fast", confidence=0.6
    )

    debate_calls = [c for c in mock_bus.publish.call_args_list if "debate" in str(c)]
    assert len(debate_calls) == 0


@pytest.mark.asyncio
async def test_same_agent_no_debate_trigger():
    mock_bus = AsyncMock()
    mock_bus.publish = AsyncMock()

    board = _make_board(bus=mock_bus)
    # Same agent posts twice on same topic — no conflict with self
    await board.post(
        author_agent_id="a1", topic="perf", content="slow", confidence=0.9
    )
    await board.post(
        author_agent_id="a1", topic="perf", content="slower", confidence=0.9
    )

    debate_calls = [c for c in mock_bus.publish.call_args_list if "debate" in str(c)]
    assert len(debate_calls) == 0


@pytest.mark.asyncio
async def test_confidence_clamped_between_0_and_1():
    board = _make_board()
    entry_high = await board.post(
        author_agent_id="a1", topic="t", content="c", confidence=5.0
    )
    entry_low = await board.post(
        author_agent_id="a2", topic="t", content="c", confidence=-2.0
    )
    assert entry_high["confidence"] == 1.0
    assert entry_low["confidence"] == 0.0


# ── DB-path tests ─────────────────────────────────────────────────────────────


class _noop_ctx:
    async def __aenter__(self) -> "_noop_ctx":
        return self

    async def __aexit__(self, *_):
        return None


class _FakeDBSession:
    """Minimal async session mock for DB-path tests."""

    def __init__(self, rows=None, raise_on_execute=False):
        self.executions = []
        self._rows = rows or []
        self._raise = raise_on_execute

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def begin(self):
        return _noop_ctx()

    async def execute(self, statement, params=None):
        if self._raise:
            raise RuntimeError("DB error")
        self.executions.append((statement, params))
        from types import SimpleNamespace
        return SimpleNamespace(
            fetchall=lambda: list(self._rows),
            fetchone=lambda: self._rows[0] if self._rows else None,
        )


@pytest.mark.asyncio
async def test_post_with_db_inserts_row():
    session = _FakeDBSession()
    board = _make_board(db=lambda: session)
    entry = await board.post(
        author_agent_id="a1",
        topic="bugs",
        content="3 critical bugs found",
        confidence=0.9,
    )
    assert entry["topic"] == "bugs"
    # Should have executed at least one INSERT
    assert len(session.executions) >= 1


@pytest.mark.asyncio
async def test_post_with_db_exception_still_returns_entry():
    """DB failure during post is swallowed; entry is returned."""
    session = _FakeDBSession(raise_on_execute=True)
    board = _make_board(db=lambda: session)
    entry = await board.post(
        author_agent_id="a1",
        topic="test",
        content="some content",
        confidence=0.8,
    )
    # Should return something even when DB fails
    assert entry is not None


@pytest.mark.asyncio
async def test_post_with_db_and_bus_publishes():
    session = _FakeDBSession()
    mock_bus = AsyncMock()
    mock_bus.publish = AsyncMock()
    board = _make_board(db=lambda: session, bus=mock_bus)
    await board.post(
        author_agent_id="a1",
        topic="findings",
        content="found something",
        confidence=0.8,
    )
    # Bus should be published to
    mock_bus.publish.assert_called()


@pytest.mark.asyncio
async def test_post_with_bus_publish_exception_still_returns():
    """Bus publish failures are swallowed."""
    mock_bus = AsyncMock()
    mock_bus.publish = AsyncMock(side_effect=RuntimeError("Bus down"))
    board = _make_board(bus=mock_bus)
    entry = await board.post(
        author_agent_id="a1",
        topic="findings",
        content="test",
        confidence=0.8,
    )
    assert entry is not None


@pytest.mark.asyncio
async def test_update_with_db_success():
    """update() via DB path returns updated version."""
    # Row: version = 2 (after update)
    session = _FakeDBSession(rows=[(2,)])
    board = _make_board(db=lambda: session)
    result = await board.update(
        entry_id="e1",
        author_agent_id="a1",
        content="updated content",
        confidence=0.85,
        expected_version=1,
    )
    assert result["id"] == "e1"
    assert result["version"] == 2


@pytest.mark.asyncio
async def test_update_with_db_version_conflict_raises():
    """DB UPDATE returns no rows → version conflict → BlackboardConflictError."""
    from app.civilization.blackboard import BlackboardConflictError

    # First execute (UPDATE) returns no row; second (SELECT) returns version=3
    call_count = 0

    class _ConflictSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def begin(self):
            return _noop_ctx()

        async def execute(self, statement, params=None):
            nonlocal call_count
            from types import SimpleNamespace
            call_count += 1
            if call_count == 1:
                # UPDATE returned nothing
                return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])
            else:
                # SELECT current version
                return SimpleNamespace(fetchone=lambda: (3,), fetchall=lambda: [(3,)])

    board = _make_board(db=lambda: _ConflictSession())
    with pytest.raises(BlackboardConflictError) as exc_info:
        await board.update(
            entry_id="e1",
            author_agent_id="a1",
            content="stale update",
            confidence=0.9,
            expected_version=1,
        )
    assert exc_info.value.expected_version == 1
    assert exc_info.value.current_version == 3


@pytest.mark.asyncio
async def test_update_with_db_exception_reraises():
    """Non-conflict DB exceptions are re-raised from update()."""
    session = _FakeDBSession(raise_on_execute=True)
    board = _make_board(db=lambda: session)
    with pytest.raises(RuntimeError):
        await board.update(
            entry_id="e1",
            author_agent_id="a1",
            content="x",
            confidence=0.8,
            expected_version=1,
        )


@pytest.mark.asyncio
async def test_update_in_memory_missing_entry_raises_key_error():
    """Updating a non-existent in-memory entry raises KeyError."""
    board = _make_board()
    with pytest.raises(KeyError):
        await board.update(
            entry_id="NONEXISTENT_ID",
            author_agent_id="a1",
            content="update",
            confidence=0.9,
            expected_version=1,
        )


@pytest.mark.asyncio
async def test_query_with_db_success():
    """query() via DB path returns mapped rows."""
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    rows = [
        ("e1", "a1", "bugs", "3 bugs found", 0.9, [], 1, now),
        ("e2", "a2", "bugs", "5 bugs found", 0.8, [], 2, now),
    ]
    session = _FakeDBSession(rows=rows)
    board = _make_board(db=lambda: session)
    results = await board.query(topic="bugs", min_confidence=0.7, limit=10)
    assert len(results) == 2
    assert results[0]["id"] == "e1"
    assert results[0]["confidence"] == 0.9
    assert results[0]["civilization_id"] == "civ-1"


@pytest.mark.asyncio
async def test_query_with_db_and_filters():
    """query() with topic and author_agent_id filters adds them to params."""
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    rows = [("e1", "a1", "perf", "content", 0.9, [], 1, now)]
    session = _FakeDBSession(rows=rows)
    board = _make_board(db=lambda: session)
    results = await board.query(
        topic="perf",
        author_agent_id="a1",
        min_confidence=0.5,
        limit=20,
    )
    assert len(results) == 1
    _, params = session.executions[0]
    assert params["topic"] == "perf"
    assert params["author"] == "a1"


@pytest.mark.asyncio
async def test_query_with_db_exception_returns_empty():
    session = _FakeDBSession(raise_on_execute=True)
    board = _make_board(db=lambda: session)
    results = await board.query(topic="bugs")
    assert results == []


@pytest.mark.asyncio
async def test_query_with_db_none_ts_becomes_empty_string():
    """Rows with None created_at produce empty string ts."""
    rows = [("e1", "a1", "topic", "content", 0.9, [], 1, None)]
    session = _FakeDBSession(rows=rows)
    board = _make_board(db=lambda: session)
    results = await board.query()
    assert results[0]["created_at"] == ""


@pytest.mark.asyncio
async def test_conflict_with_bus_publish_exception_is_swallowed():
    """If bus.publish raises during conflict detection, no exception propagates."""
    mock_bus = AsyncMock()
    mock_bus.publish = AsyncMock(side_effect=RuntimeError("Bus error"))
    board = _make_board(bus=mock_bus)
    # Post first high-confidence claim
    await board.post(
        author_agent_id="a1",
        topic="hotspot",
        content="slow path detected",
        confidence=0.9,
    )
    # Post conflicting high-confidence claim from different agent — should not raise
    entry = await board.post(
        author_agent_id="a2",
        topic="hotspot",
        content="no slow path",
        confidence=0.9,
    )
    assert entry is not None
