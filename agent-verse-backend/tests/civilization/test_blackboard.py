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
