"""CORE-06: the debate vote sees the critiques, counts only valid votes, and
reports the proposal it actually chose; the API does not accept rounds the
orchestrator would silently cap.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent.debate import DebateOrchestrator
from app.providers.base import CompletionResponse


class _ScriptedProvider:
    """Answers by prompt kind so asyncio.gather ordering cannot shuffle replies."""

    def __init__(self, votes: dict[str, str]) -> None:
        self.votes = votes
        self.prompts: list[str] = []

    async def complete(self, request: Any) -> CompletionResponse:
        text = request.messages[-1].content
        self.prompts.append(text)
        speaker = text.split("As ", 1)[1].split(",", 1)[0].split(" ", 1)[0] if "As " in text else ""
        if "Propose your best solution" in text:
            me = text.split("You are ", 1)[1].split(",", 1)[0]
            content = f"PROPOSAL-{me}"
        elif "critique this proposal" in text:
            target = text.split("from ", 1)[1].split(" ", 1)[0]
            content = f"CRITIQUE-{speaker}-on-{target}"
        else:
            content = self.votes[speaker]
        return CompletionResponse(content=content, model="fake", input_tokens=1, output_tokens=1)


async def test_vote_prompt_contains_the_critiques() -> None:
    provider = _ScriptedProvider({"agent_1": "agent_2", "agent_2": "agent_1", "agent_3": "agent_1"})
    await DebateOrchestrator(provider=provider, n_agents=3, rounds=2).run("pick a db")

    vote_prompts = [p for p in provider.prompts if "vote for the BEST" in p]
    assert len(vote_prompts) == 3
    voter_1 = next(p for p in vote_prompts if p.startswith("As agent_1,"))
    # agent_1 judges agent_2's proposal together with what agent_3 said about it.
    assert "PROPOSAL-agent_2" in voter_1
    assert "CRITIQUE-agent_3-on-agent_2" in voter_1


async def test_garbage_vote_is_discarded_and_winner_is_the_chosen_proposal() -> None:
    provider = _ScriptedProvider(
        {
            "agent_1": "I think the second one is best overall",  # no agent id
            "agent_2": "agent_3",
            "agent_3": "agent_2",
        }
    )
    result = await DebateOrchestrator(provider=provider, n_agents=3, rounds=1).run("pick")

    chosen = next(p for p in result.all_proposals if p.proposal == result.winning_proposal)
    assert result.winning_agent == chosen.agent_id
    assert result.winning_agent in {"agent_2", "agent_3"}
    # Two valid votes, one each: the winner holds half of the valid votes.
    assert result.consensus_level == pytest.approx(0.5)


async def test_self_votes_and_unknown_ids_do_not_count() -> None:
    provider = _ScriptedProvider(
        {"agent_1": "agent_1", "agent_2": "agent_9", "agent_3": "Vote: AGENT_2."}
    )
    result = await DebateOrchestrator(provider=provider, n_agents=3, rounds=1).run("pick")

    assert result.winning_agent == "agent_2"
    assert result.consensus_level == pytest.approx(1.0)  # 1 valid vote, for agent_2
    winner = next(p for p in result.all_proposals if p.agent_id == "agent_2")
    assert winner.votes_received == 1


async def test_no_valid_votes_reports_zero_consensus_and_a_real_agent() -> None:
    provider = _ScriptedProvider({"agent_1": "???", "agent_2": "no idea"})
    result = await DebateOrchestrator(provider=provider, n_agents=2, rounds=1).run("pick")

    assert result.winning_agent in {"agent_1", "agent_2"}
    chosen = next(p for p in result.all_proposals if p.agent_id == result.winning_agent)
    assert chosen.proposal == result.winning_proposal
    assert result.consensus_level == 0.0


def test_api_rejects_more_rounds_than_the_orchestrator_runs() -> None:
    from pydantic import ValidationError

    from app.api.goals import GoalRequest

    assert GoalRequest(goal="g", debate_rounds=3).debate_rounds == 3
    with pytest.raises(ValidationError):
        GoalRequest(goal="g", debate_rounds=10)
