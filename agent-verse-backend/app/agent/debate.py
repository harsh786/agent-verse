"""Debate/voting pattern — N agents independently propose solutions,
critique each other, then vote on the best approach.

Reduces hallucination and improves accuracy for high-stakes decisions.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# The largest ``rounds`` the orchestrator runs (proposals, critiques, vote). The
# API bounds ``debate_rounds`` with the same number so nothing is silently cut.
MAX_DEBATE_ROUNDS = 3
_AGENT_ID_RE = re.compile(r"agent_\d+")


def parse_vote(raw: str, *, valid_ids: set[str]) -> str | None:
    """The agent id a vote names, or None when it names no eligible agent.

    Only ids in *valid_ids* (the other debaters) count; a reply that names none —
    or only the voter itself — is not a vote. It used to be tallied verbatim, so
    garbage could "win" and be reported as the winning agent.
    """
    for candidate in _AGENT_ID_RE.findall((raw or "").lower()):
        if candidate in valid_ids:
            return candidate
    return None


@dataclass
class AgentProposal:
    agent_id: str
    proposal: str
    confidence: float = 0.5
    critique_of: dict[str, str] = field(default_factory=dict)  # agent_id → critique
    votes_received: int = 0


@dataclass
class DebateResult:
    winning_proposal: str
    winning_agent: str
    all_proposals: list[AgentProposal]
    consensus_level: float  # 0.0-1.0
    rounds: int = 2


class DebateOrchestrator:
    """Run N agents in a debate to find the best solution via voting."""

    def __init__(
        self,
        *,
        provider: Any,
        n_agents: int = 3,
        rounds: int = 2,
    ) -> None:
        self._provider = provider
        self._n_agents = max(2, min(n_agents, 5))  # 2-5 agents
        self._rounds = max(1, min(rounds, MAX_DEBATE_ROUNDS))  # 1-3 rounds

    async def run(
        self,
        goal: str,
        context: str = "",
        event_callback: Any = None,
    ) -> DebateResult:
        """Run multi-agent debate and return winning proposal."""

        async def emit(event: dict) -> None:
            if event_callback:
                with contextlib.suppress(Exception):
                    await event_callback(event)

        agent_ids = [f"agent_{i + 1}" for i in range(self._n_agents)]

        await emit({"type": "debate_started", "n_agents": self._n_agents, "rounds": self._rounds})

        # Round 1: Independent proposals
        async def propose(agent_id: str) -> AgentProposal:
            from app.providers.base import CompletionRequest, Message

            req = CompletionRequest(
                messages=[
                    Message(
                        role="user",
                        content=(
                            f"You are {agent_id}, an expert agent. "
                            f"Propose your best solution to this goal:\n\n{goal}"
                            + (f"\n\nContext: {context}" if context else "")
                            + "\n\nGive a specific, actionable proposal in 2-3 sentences."
                        ),
                    )
                ],
                model="",
            )
            from app.providers.guarded_completion import (
                complete_decision,
                generation_timeout_seconds,
            )

            resp = await complete_decision(
                self._provider,
                req,
                role="debate_propose",
                timeout_seconds=generation_timeout_seconds(),
            )
            return AgentProposal(agent_id=agent_id, proposal=resp.content)

        proposals = list(await asyncio.gather(*[propose(aid) for aid in agent_ids]))
        await emit({"type": "debate_proposals_ready", "count": len(proposals)})

        # Round 2 (if enabled): Critiques
        if self._rounds >= 2:

            async def critique(proposer: AgentProposal) -> None:
                from app.providers.base import CompletionRequest, Message

                others = [p for p in proposals if p.agent_id != proposer.agent_id]
                for other in others:
                    req = CompletionRequest(
                        messages=[
                            Message(
                                role="user",
                                content=(
                                    f"As {proposer.agent_id}, briefly critique this proposal "
                                    f"from {other.agent_id} for solving: {goal}\n\n"
                                    f"Their proposal: {other.proposal}\n\n"
                                    "Give a 1-sentence critique."
                                ),
                            )
                        ],
                        model="",
                    )
                    from app.providers.guarded_completion import (
                        complete_decision,
                        generation_timeout_seconds,
                    )

                    resp = await complete_decision(
                        self._provider,
                        req,
                        role="debate_critique",
                        timeout_seconds=generation_timeout_seconds(),
                    )
                    proposer.critique_of[other.agent_id] = resp.content

            await asyncio.gather(*[critique(p) for p in proposals])

        # Final: Vote
        async def vote(voter: AgentProposal) -> str:
            from app.providers.base import CompletionRequest, Message

            # Each candidate proposal is shown with the critiques the other
            # debaters wrote about it — they used to be generated and then never
            # shown to anyone, so round 2 could not influence the vote.
            def _entry(i: int, p: AgentProposal) -> str:
                critiques = [
                    f"   - {critic.agent_id}: {critic.critique_of[p.agent_id]}"
                    for critic in proposals
                    if p.agent_id in critic.critique_of
                ]
                block = f"{i + 1}. [{p.agent_id}] {p.proposal}"
                if critiques:
                    block += "\n   Critiques:\n" + "\n".join(critiques)
                return block

            proposal_list = "\n".join(
                _entry(i, p) for i, p in enumerate(proposals) if p.agent_id != voter.agent_id
            )
            req = CompletionRequest(
                messages=[
                    Message(
                        role="user",
                        content=(
                            f"As {voter.agent_id}, vote for the BEST proposal (not your own) "
                            f"for: {goal}\n\nProposals:\n{proposal_list}\n\n"
                            "Reply with just the agent_id of who you vote for (e.g., 'agent_2')."
                        ),
                    )
                ],
                model="",
            )
            from app.providers.guarded_completion import complete_decision

            resp = await complete_decision(
                self._provider,
                req,
                role="debate_vote",
            )
            return resp.content.strip()

        raw_votes = await asyncio.gather(*[vote(p) for p in proposals])

        # Tally only valid votes: a named other debater. Invalid replies are
        # discarded (and counted) instead of being tallied as if they were ids.
        valid_votes: list[str] = []
        for voter, raw in zip(proposals, raw_votes, strict=True):
            eligible = {p.agent_id for p in proposals if p.agent_id != voter.agent_id}
            choice = parse_vote(raw, valid_ids=eligible)
            if choice is not None:
                valid_votes.append(choice)
        invalid_votes = len(raw_votes) - len(valid_votes)
        if invalid_votes:
            logger.warning("debate_invalid_votes", invalid=invalid_votes, total=len(raw_votes))

        tally = Counter(valid_votes)
        for p in proposals:
            p.votes_received = tally.get(p.agent_id, 0)
        # Winner = most valid votes (first proposal on a tie / no valid votes);
        # the reported agent is always the proposal actually chosen.
        winner = max(proposals, key=lambda p: p.votes_received)
        winning_agent_id = winner.agent_id

        # Consensus level = valid votes for the winner / all valid votes.
        consensus = winner.votes_received / len(valid_votes) if valid_votes else 0.0

        result = DebateResult(
            winning_proposal=winner.proposal,
            winning_agent=winning_agent_id,
            all_proposals=proposals,
            consensus_level=consensus,
            rounds=self._rounds,
        )

        await emit(
            {
                "type": "debate_complete",
                "winner": winning_agent_id,
                "votes": winner.votes_received,
                "invalid_votes": invalid_votes,
                "consensus": round(consensus, 2),
            }
        )

        return result
