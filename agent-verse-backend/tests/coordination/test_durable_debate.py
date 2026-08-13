from __future__ import annotations

import pytest

from app.coordination.patterns.common import InMemoryPatternCheckpointStore
from app.coordination.patterns.debate_adapter import DurableDebateRuntime


@pytest.mark.asyncio
async def test_debate_persists_independent_proposals_votes_dissent_and_resume() -> None:
    store = InMemoryPatternCheckpointStore()
    proposals: list[str] = []

    async def propose(agent_id: str):
        proposals.append(agent_id)
        return {
            "proposal_reference": f"proposal://{agent_id}",
            "safe_summary": f"proposal by {agent_id}",
        }

    runtime = DurableDebateRuntime(checkpoint_store=store)
    state = await runtime.execute(
        session_id="session",
        execution_id="execution",
        participant_ids=("a", "b", "c"),
        propose=propose,
        critique=lambda source, target: f"critique://{source}/{target}",
        vote=lambda voter, _items: {"a": "b", "b": "a", "c": "a"}[voter],
        quorum=2,
    )
    assert state.phase == "completed" and state.winner_agent_id == "a"
    assert state.dissenting_agent_ids == ("a",)
    resumed = await runtime.execute(
        session_id="session",
        execution_id="execution",
        participant_ids=("a", "b", "c"),
        propose=lambda _: pytest.fail("must not repropose"),
        critique=lambda *_: pytest.fail("must not re-critique"),
        vote=lambda *_: pytest.fail("must not revote"),
        quorum=2,
    )
    assert resumed == state and proposals == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_debate_rejects_invalid_vote_and_escalates_quorum_loss() -> None:
    runtime = DurableDebateRuntime(checkpoint_store=InMemoryPatternCheckpointStore())
    common = {
        "session_id": "session",
        "participant_ids": ("a", "b", "c"),
        "propose": lambda agent: {
            "proposal_reference": f"proposal://{agent}",
            "safe_summary": agent,
        },
        "critique": lambda source, target: f"critique://{source}/{target}",
    }
    with pytest.raises(ValueError, match="nonexistent"):
        await runtime.execute(
            **common,
            execution_id="invalid",
            vote=lambda *_: "missing",
            quorum=2,
        )
    split = await runtime.execute(
        **common,
        execution_id="split",
        vote=lambda voter, _: {"a": "b", "b": "c", "c": "a"}[voter],
        quorum=2,
    )
    assert split.phase == "failed" and split.requires_hitl
