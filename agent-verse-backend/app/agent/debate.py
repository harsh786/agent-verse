"""Debate/voting pattern — N agents independently propose solutions,
critique each other, then vote on the best approach.

Reduces hallucination and improves accuracy for high-stakes decisions.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


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
        self._rounds = max(1, min(rounds, 3))  # 1-3 rounds

    async def run(
        self,
        goal: str,
        context: str = "",
        event_callback: Any = None,
    ) -> DebateResult:
        """Run multi-agent debate and return winning proposal."""

        async def emit(event: dict) -> None:
            if event_callback:
                try:
                    await event_callback(event)
                except Exception:
                    pass

        agent_ids = [f"agent_{i+1}" for i in range(self._n_agents)]

        await emit({"type": "debate_started", "n_agents": self._n_agents, "rounds": self._rounds})

        # Round 1: Independent proposals
        async def propose(agent_id: str) -> AgentProposal:
            from app.providers.base import CompletionRequest, Message
            req = CompletionRequest(
                messages=[Message(role="user", content=(
                    f"You are {agent_id}, an expert agent. "
                    f"Propose your best solution to this goal:\n\n{goal}"
                    + (f"\n\nContext: {context}" if context else "")
                    + "\n\nGive a specific, actionable proposal in 2-3 sentences."
                ))],
                model="",
            )
            resp = await self._provider.complete(req)
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
                        messages=[Message(role="user", content=(
                            f"As {proposer.agent_id}, briefly critique this proposal "
                            f"from {other.agent_id} for solving: {goal}\n\n"
                            f"Their proposal: {other.proposal}\n\n"
                            "Give a 1-sentence critique."
                        ))],
                        model="",
                    )
                    resp = await self._provider.complete(req)
                    proposer.critique_of[other.agent_id] = resp.content

            await asyncio.gather(*[critique(p) for p in proposals])

        # Final: Vote
        async def vote(voter: AgentProposal) -> str:
            from app.providers.base import CompletionRequest, Message
            proposal_list = "\n".join([
                f"{i+1}. [{p.agent_id}] {p.proposal}"
                for i, p in enumerate(proposals)
                if p.agent_id != voter.agent_id
            ])
            req = CompletionRequest(
                messages=[Message(role="user", content=(
                    f"As {voter.agent_id}, vote for the BEST proposal (not your own) "
                    f"for: {goal}\n\nProposals:\n{proposal_list}\n\n"
                    "Reply with just the agent_id of who you vote for (e.g., 'agent_2')."
                ))],
                model="",
            )
            resp = await self._provider.complete(req)
            return resp.content.strip()

        votes = await asyncio.gather(*[vote(p) for p in proposals])

        # Tally votes
        from collections import Counter
        tally = Counter(votes)
        winning_agent_id = tally.most_common(1)[0][0] if tally else agent_ids[0]

        # Find winning proposal
        winner = next(
            (p for p in proposals if p.agent_id == winning_agent_id),
            proposals[0]
        )
        winner.votes_received = tally.get(winning_agent_id, 0)

        # Consensus level = votes for winner / total votes
        consensus = winner.votes_received / max(len(votes), 1)

        result = DebateResult(
            winning_proposal=winner.proposal,
            winning_agent=winning_agent_id,
            all_proposals=proposals,
            consensus_level=consensus,
            rounds=self._rounds,
        )

        await emit({
            "type": "debate_complete",
            "winner": winning_agent_id,
            "votes": winner.votes_received,
            "consensus": round(consensus, 2),
        })

        return result
