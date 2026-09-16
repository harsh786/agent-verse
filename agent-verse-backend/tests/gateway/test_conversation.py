"""Tests for app/gateway/conversation.py — ConversationManager (multi-turn context).

Covers:
  - get_or_create: existing conversation found, none found (fresh in-memory), query exception
  - add_turn: success path, exception swallowed
  - get_context: last_n slicing, no row, exception
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway.conversation import Conversation, ConversationManager, ConversationTurn

TENANT_ID = "t-conv-test"
ORG_ID = "org-conv-test"


def _make_session(execute_result: MagicMock | None = None) -> AsyncMock:
    session = AsyncMock()
    if execute_result is not None:
        session.execute = AsyncMock(return_value=execute_result)
    return session


class TestGetOrCreate:
    @pytest.mark.asyncio
    async def test_existing_conversation_found(self) -> None:
        row = (
            "conv-1",
            [
                {
                    "role": "user",
                    "content": "hi",
                    "timestamp": "2024-01-01T00:00:00",
                    "channel": "rest",
                    "command_id": None,
                }
            ],
            "summary text",
            datetime.now(UTC),
            datetime.now(UTC),
        )
        result_mock = MagicMock()
        result_mock.fetchone = MagicMock(return_value=row)
        session = _make_session(result_mock)
        manager = ConversationManager(session)

        conv = await manager.get_or_create(TENANT_ID, ORG_ID, "rest", "user-1", None)
        assert isinstance(conv, Conversation)
        assert conv.id == "conv-1"
        assert len(conv.turns) == 1
        assert isinstance(conv.turns[0], ConversationTurn)
        assert conv.context_summary == "summary text"

    @pytest.mark.asyncio
    async def test_no_existing_conversation_creates_new(self) -> None:
        result_mock = MagicMock()
        result_mock.fetchone = MagicMock(return_value=None)
        session = _make_session(result_mock)
        manager = ConversationManager(session)

        conv = await manager.get_or_create(TENANT_ID, ORG_ID, "slack", "u1", "key1")
        assert conv.turns == []
        assert conv.tenant_id == TENANT_ID
        assert conv.channel == "slack"
        assert conv.id  # uuid4 generated

    @pytest.mark.asyncio
    async def test_db_query_exception_falls_back_to_new_conversation(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=RuntimeError("no such table"))
        manager = ConversationManager(session)

        conv = await manager.get_or_create(TENANT_ID, ORG_ID, "email", None, None)
        assert conv.turns == []
        assert conv.org_id == ORG_ID
        assert conv.channel_user_id is None


class TestAddTurn:
    @pytest.mark.asyncio
    async def test_add_turn_success(self) -> None:
        session = AsyncMock()
        manager = ConversationManager(session)
        await manager.add_turn(
            conversation_id="conv-1",
            tenant_id=TENANT_ID,
            role="user",
            content="Hello there",
            channel="rest",
            command_id="cmd-1",
        )
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_add_turn_exception_is_swallowed(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=RuntimeError("db gone"))
        manager = ConversationManager(session)
        # Should not raise despite the underlying execute() failing.
        await manager.add_turn(
            conversation_id="conv-1",
            tenant_id=TENANT_ID,
            role="assistant",
            content="Response",
            channel="rest",
        )
        session.commit.assert_not_awaited()


class TestGetContext:
    @pytest.mark.asyncio
    async def test_returns_last_n_turns(self) -> None:
        turns_raw = [
            {
                "role": "user",
                "content": f"msg{i}",
                "timestamp": "2024-01-01T00:00:00",
                "channel": "rest",
                "command_id": None,
            }
            for i in range(15)
        ]
        result_mock = MagicMock()
        result_mock.fetchone = MagicMock(return_value=(turns_raw,))
        session = _make_session(result_mock)
        manager = ConversationManager(session)

        turns = await manager.get_context("conv-1", last_n=5)
        assert len(turns) == 5
        assert turns[-1].content == "msg14"

    @pytest.mark.asyncio
    async def test_no_row_returns_empty_list(self) -> None:
        result_mock = MagicMock()
        result_mock.fetchone = MagicMock(return_value=None)
        session = _make_session(result_mock)
        manager = ConversationManager(session)

        turns = await manager.get_context("conv-missing")
        assert turns == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty_list(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=RuntimeError("boom"))
        manager = ConversationManager(session)

        turns = await manager.get_context("conv-1")
        assert turns == []
