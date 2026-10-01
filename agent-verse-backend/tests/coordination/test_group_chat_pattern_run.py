"""GROUP-CHAT-GOAL: group_chat is a coordination pattern run (and so a goal strategy)."""

from __future__ import annotations

from typing import Any

import pytest

from app.orchestration.execution_drivers import COORDINATION_PATTERN_STRATEGIES
from tests.coordination.goal_strategy_support import run_goal
from tests.coordination.pattern_run_support import (
    TENANT,
    ScriptedProvider,
    active_session,
    pattern_state,
    service,
)


async def _run(state: Any, session_id: str, key: str, **kw: Any) -> Any:
    return await service(state).run(
        TENANT,
        session_id,
        "group_chat",
        objective=kw.pop("objective", "Write a short market report"),
        participants=kw.pop("participants", ()),
        max_rounds=kw.pop("max_rounds", 4),
        options={},
        idempotency_key=key,
    )


def test_group_chat_is_a_runner_strategy() -> None:
    assert "group_chat" in COORDINATION_PATTERN_STRATEGIES


@pytest.mark.asyncio
async def test_round_robin_chat_ends_when_a_participant_concludes() -> None:
    state = pattern_state(ScriptedProvider())
    session_id = await active_session(state)
    result = await _run(state, session_id, "run-1")
    assert result["phase"] == "completed", result
    assert result["safe_output"] == "GROUP CHAT ANSWER"
    assert result["view"]["concluded_by"] == "writer"
    assert result["view"]["rounds"] == 3  # planner, critic, writer
    page = await state.transcript_service.page(
        TENANT.tenant_id, session_id, after_sequence=0, limit=50
    )
    assert [m.sender_agent_id for m in page] == ["planner", "critic", "writer"]


@pytest.mark.asyncio
async def test_two_chats_in_one_session_keep_separate_transcripts() -> None:
    state = pattern_state(ScriptedProvider())
    session_id = await active_session(state)
    await _run(state, session_id, "run-1")
    await _run(state, session_id, "run-2", objective="A different objective")
    page = await state.transcript_service.page(
        TENANT.tenant_id, session_id, after_sequence=0, limit=50
    )
    assert len(page) == 6  # the second run's turns were not deduplicated into the first's


@pytest.mark.asyncio
async def test_a_chat_that_never_concludes_fails_at_the_round_limit() -> None:
    state = pattern_state(ScriptedProvider())
    session_id = await active_session(state)
    result = await _run(state, session_id, "run-1", participants=("alice", "bob"), max_rounds=2)
    assert result["phase"] == "failed"
    assert result["terminal_reason"] == "round_limit"
    assert not result["safe_output"]


@pytest.mark.asyncio
async def test_goal_selecting_group_chat_runs_on_a_goal_linked_session() -> None:
    provider = ScriptedProvider()
    state = pattern_state(provider)
    result, events = await run_goal(
        state,
        "group_chat",
        goal="Write a short market report",
        goal_id="goal-group-chat",
        tenant_ctx=TENANT,
        provider=provider,
    )
    assert result["terminal_state"] == "succeeded", events[-1]
    assert result["answer"] == "GROUP CHAT ANSWER"
    assert events[-1]["type"] == "goal_complete"
    assert any(e["type"] == "coordination_progress" for e in events)
