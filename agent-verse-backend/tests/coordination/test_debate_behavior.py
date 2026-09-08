"""Behavioral tests for the durable Debate adapter (proposal -> critique -> vote)."""

from __future__ import annotations

import pytest

from app.coordination.patterns.common import InMemoryPatternCheckpointStore
from app.coordination.patterns.debate_adapter import (
    DebateProposal,
    DurableDebateRuntime,
    DurableDebateState,
)


def _propose(agent_id: str) -> dict[str, str]:
    return {"proposal_reference": f"proposal://{agent_id}", "safe_summary": agent_id}


@pytest.mark.asyncio
async def test_debate_critique_is_full_fan_out_over_other_proposals() -> None:
    runtime = DurableDebateRuntime(checkpoint_store=InMemoryPatternCheckpointStore())
    critique_pairs: list[tuple[str, str]] = []

    def critique(source: str, target: str) -> str:
        critique_pairs.append((source, target))
        return f"critique://{source}/{target}"

    state = await runtime.execute(
        session_id="s",
        execution_id="critique",
        participant_ids=("a", "b", "c"),
        propose=_propose,
        critique=critique,
        vote=lambda voter, _items: {"a": "b", "b": "a", "c": "a"}[voter],
        quorum=2,
    )
    # Every proposal critiques each of the two others: 3 * 2 = 6 ordered pairs, no self.
    assert set(critique_pairs) == {
        ("a", "b"),
        ("a", "c"),
        ("b", "a"),
        ("b", "c"),
        ("c", "a"),
        ("c", "b"),
    }
    assert all(source != target for source, target in critique_pairs)
    by_agent = {p.agent_id: p for p in state.proposals}
    assert all(len(p.critique_references) == 2 for p in by_agent.values())
    assert by_agent["a"].critique_references == ("critique://a/b", "critique://a/c")


@pytest.mark.asyncio
async def test_debate_resumes_from_persisted_critiqued_phase_without_reproposing() -> None:
    store = InMemoryPatternCheckpointStore()
    seeded = DurableDebateState(
        session_id="s",
        execution_id="resume",
        phase="critiqued",
        participant_ids=("a", "b", "c"),
        proposals=(
            DebateProposal(agent_id="a", proposal_reference="p://a", safe_summary="a"),
            DebateProposal(agent_id="b", proposal_reference="p://b", safe_summary="b"),
            DebateProposal(agent_id="c", proposal_reference="p://c", safe_summary="c"),
        ),
    )
    await store.save(seeded)
    runtime = DurableDebateRuntime(checkpoint_store=store)
    state = await runtime.execute(
        session_id="s",
        execution_id="resume",
        participant_ids=("a", "b", "c"),
        propose=lambda _agent: pytest.fail("must not propose on critiqued resume"),
        critique=lambda *_a: pytest.fail("must not critique on critiqued resume"),
        vote=lambda voter, _items: {"a": "b", "b": "c", "c": "b"}[voter],
        quorum=2,
    )
    # Only the voting phase runs; winner b has 2 votes (b, c) which meets quorum.
    assert state.phase == "completed" and state.winner_agent_id == "b"
    assert not state.requires_hitl


@pytest.mark.asyncio
async def test_debate_winner_tie_break_is_deterministic_by_agent_id() -> None:
    runtime = DurableDebateRuntime(checkpoint_store=InMemoryPatternCheckpointStore())
    state = await runtime.execute(
        session_id="s",
        execution_id="tie",
        participant_ids=("b", "a"),
        propose=_propose,
        critique=lambda source, target: f"c://{source}/{target}",
        # a votes for b, b votes for a -> 1 vote each, a true tie.
        vote=lambda voter, _items: {"a": "b", "b": "a"}[voter],
        quorum=1,
    )
    # Tie is broken by the lexicographically smallest agent id.
    assert state.winner_agent_id == "a"
    assert state.phase == "completed"
    # The single voter who did not back the winner is recorded as dissent.
    assert state.dissenting_agent_ids == ("a",)


@pytest.mark.asyncio
async def test_debate_rejects_degenerate_participants_and_quorum() -> None:
    runtime = DurableDebateRuntime(checkpoint_store=InMemoryPatternCheckpointStore())
    base = {
        "session_id": "s",
        "propose": _propose,
        "critique": lambda source, target: "c",
        "vote": lambda voter, _items: "a",
    }
    with pytest.raises(ValueError, match="distinct participants"):
        await runtime.execute(
            **base, execution_id="one", participant_ids=("solo",), quorum=1
        )
    with pytest.raises(ValueError, match="distinct participants"):
        await runtime.execute(
            **base, execution_id="dup", participant_ids=("a", "a"), quorum=1
        )
    with pytest.raises(ValueError, match="quorum"):
        await runtime.execute(
            **base, execution_id="hi", participant_ids=("a", "b"), quorum=3
        )
    with pytest.raises(ValueError, match="quorum"):
        await runtime.execute(
            **base, execution_id="lo", participant_ids=("a", "b"), quorum=0
        )
