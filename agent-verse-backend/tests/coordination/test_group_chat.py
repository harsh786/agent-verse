from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.coordination.group_chat.adapter import GroupChatRuntime
from app.coordination.group_chat.models import (
    GroupChatExecutionState,
    GroupChatParticipant,
)
from app.coordination.group_chat.speaker_policy import (
    AgentBasedSpeakerPolicy,
    RoundRobinSpeakerPolicy,
    RuleBasedSpeakerPolicy,
)
from app.coordination.transcript.repository import InMemoryTranscriptRepository
from app.coordination.transcript.service import TranscriptService


def _state(*, participants: tuple[GroupChatParticipant, ...] | None = None):
    return GroupChatExecutionState(
        tenant_id="tenant",
        session_id="session",
        participants=participants
        or (GroupChatParticipant(agent_id="a"), GroupChatParticipant(agent_id="b")),
        deadline=datetime.now(UTC) + timedelta(minutes=1),
    )


@pytest.mark.asyncio
async def test_round_robin_persists_turns_and_explicitly_completes() -> None:
    transcript = TranscriptService(InMemoryTranscriptRepository())
    checkpoints = []
    runtime = GroupChatRuntime(
        transcript=transcript,
        speaker_policy=RoundRobinSpeakerPolicy(),
        checkpoint_callback=checkpoints.append,
    )
    result = await runtime.execute(
        _state(),
        produce_turn=lambda speaker, _context: {
            "content": f"turn from {speaker}",
            "tokens": 10,
            "cost_usd": 0.01,
        },
        terminate=lambda state, _messages: state.round_number == 3,
    )
    assert result.state == "completed"
    assert result.round_number == 3 and result.token_count == 30
    messages = await transcript.page("tenant", "session", after_sequence=0, limit=10)
    assert [item.sender_agent_id for item in messages] == ["a", "b", "a"]
    assert checkpoints[-1].state == "completed"


@pytest.mark.asyncio
async def test_human_wait_cancellation_and_limits_are_terminal() -> None:
    transcript = TranscriptService(InMemoryTranscriptRepository())
    human = _state(participants=(GroupChatParticipant(agent_id="human", is_human=True),))
    waiting = await GroupChatRuntime(
        transcript=transcript, speaker_policy=RoundRobinSpeakerPolicy()
    ).execute(
        human,
        produce_turn=lambda *_: pytest.fail("human turn must wait"),
        terminate=lambda *_: False,
    )
    assert waiting.state == "awaiting_human"
    resumed = await GroupChatRuntime(
        transcript=transcript, speaker_policy=RoundRobinSpeakerPolicy()
    ).resume_human_turn(
        waiting,
        human_agent_id="human",
        content="approved",
        idempotency_key="human-1",
    )
    assert resumed.state == "active" and resumed.round_number == 1
    human_messages = await transcript.page("tenant", "session", limit=10)
    assert human_messages[-1].message_type == "human"
    cancelled = asyncio.Event()
    cancelled.set()
    stopped = await GroupChatRuntime(
        transcript=transcript, speaker_policy=RoundRobinSpeakerPolicy()
    ).execute(
        _state(),
        produce_turn=lambda *_: pytest.fail("cancelled run must not speak"),
        terminate=lambda *_: False,
        cancelled=cancelled,
    )
    assert stopped.state == "cancelled"
    limited = await GroupChatRuntime(
        transcript=transcript, speaker_policy=RoundRobinSpeakerPolicy()
    ).execute(
        _state(),
        produce_turn=lambda *_: {"content": "continue"},
        terminate=lambda *_: False,
        maximum_rounds=2,
    )
    assert limited.state == "failed" and limited.terminal_reason == "round_limit"


@pytest.mark.asyncio
async def test_turn_that_exceeds_hard_budget_is_not_appended() -> None:
    transcript = TranscriptService(InMemoryTranscriptRepository())
    result = await GroupChatRuntime(
        transcript=transcript, speaker_policy=RoundRobinSpeakerPolicy()
    ).execute(
        _state(),
        produce_turn=lambda *_: {"content": "too expensive", "tokens": 101},
        terminate=lambda *_: False,
        maximum_tokens=100,
    )
    assert result.state == "failed" and result.terminal_reason == "token_limit"
    assert await transcript.page("tenant", "session", limit=10) == ()


@pytest.mark.asyncio
async def test_all_speaker_policies_reject_inactive_selection() -> None:
    active = ("a", "b")
    assert RoundRobinSpeakerPolicy().select(active, turn=1).agent_id == "b"
    assert RuleBasedSpeakerPolicy(lambda agents, _: agents[0]).select(
        active, context={}
    ).agent_id == "a"
    with pytest.raises(ValueError, match="inactive"):
        RuleBasedSpeakerPolicy(lambda _agents, _: "missing").select(active, context={})
    with pytest.raises(ValueError, match="inactive"):
        await AgentBasedSpeakerPolicy().select(
            active,
            selector=lambda *_: asyncio.sleep(0, result="missing"),
            context={},
        )
