"""Agent collaboration protocol — multi-round propose/critique/counter/agree loop.

Multiple agents contribute to solving a goal collaboratively:
  1. Agent A proposes an approach (round_type="propose")
  2. Agent B critiques it (round_type="critique")
  3. Agent A counters or refines (round_type="counter")
  4. Agent B agrees or disagrees (round_type="agree")
  → synthesize_consensus() returns ConsensusResult(agreed=True/False, summary)

In production collab sessions are stored in PostgreSQL and enabled via WebSocket.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger(__name__)


@dataclass
class CollabRound:
    agent_id: str
    round_type: str  # propose | critique | counter | agree | disagree
    content: str
    round_id: str = field(default_factory=lambda: __import__("uuid").uuid4().hex)


@dataclass
class ConsensusResult:
    agreed: bool
    summary: str = ""
    dissenter: str | None = None


class AgentCollabSession:
    """Tracks a multi-agent collaboration session."""

    def __init__(self, *, goal: str) -> None:
        self.goal = goal
        self.rounds: list[CollabRound] = []

    def add_round(self, round_: CollabRound) -> None:
        self.rounds.append(round_)

    def synthesize_consensus(self) -> ConsensusResult:
        """Return ConsensusResult based on whether all recent rounds agree."""
        agree_rounds = [r for r in self.rounds if r.round_type == "agree"]
        disagree_rounds = [r for r in self.rounds if r.round_type == "disagree"]

        if not agree_rounds:
            return ConsensusResult(agreed=False, summary="No agreement round yet")
        if disagree_rounds:
            return ConsensusResult(
                agreed=False,
                summary=f"Dissent: {disagree_rounds[-1].content}",
                dissenter=disagree_rounds[-1].agent_id,
            )
        last_proposal = next(
            (r.content for r in reversed(self.rounds) if r.round_type in ("propose", "counter")),
            self.goal,
        )
        return ConsensusResult(agreed=True, summary=last_proposal)

    async def synthesize_consensus_llm(
        self,
        provider: Any,
    ) -> ConsensusResult:
        """Use LLM to synthesize consensus from all collaboration rounds."""
        if not self.rounds or provider is None:
            return self.synthesize_consensus()

        rounds_summary = [
            {"agent": r.agent_id, "type": r.round_type, "content": r.content[:300]}
            for r in self.rounds
        ]

        try:
            import json as _json
            from app.providers.base import CompletionRequest, Message
            req = CompletionRequest(
                messages=[Message(
                    role="user",
                    content=(
                        f"Analyze these collaboration rounds and produce a consensus:\n"
                        f"{_json.dumps(rounds_summary, indent=2)}\n\n"
                        f'Return JSON: {{"consensus": "...", "agreed": true|false, '
                        f'"key_points": ["..."], "dissenter_id": null}}'
                    )
                )],
                model="",
            )
            resp = await provider.complete(req)
            import re
            m = re.search(r'\{[\s\S]*\}', resp.content)
            if m:
                data = _json.loads(m.group())
                return ConsensusResult(
                    agreed=data.get("agreed", False),
                    summary=data.get("consensus", ""),
                    dissenter=data.get("dissenter_id"),
                )
        except Exception:
            pass

        return self.synthesize_consensus()

    async def persist_debate(
        self,
        *,
        session_id: str,
        goal_id: str,
        tenant_id: str,
        original_goal: str,
        consensus: str,
        confidence: float,
        rounds: int,
        proposals: list[dict],
        db: Any,
    ) -> None:
        """Persist a completed debate session and its proposals to PostgreSQL.

        Parameters
        ----------
        session_id:    Unique identifier for the debate session.
        goal_id:       The goal this debate was created for.
        tenant_id:     Tenant that owns this debate.
        original_goal: The raw goal text submitted.
        consensus:     The winning/consensus proposal text.
        confidence:    Consensus level (0.0–1.0).
        rounds:        Number of debate rounds completed.
        proposals:     List of proposal dicts with keys:
                       id, role, proposal, critique, vote, round.
        db:            Async session factory (callable → AsyncSession).
                       If None, the method returns without writing.
        """
        if db is None:
            return
        try:
            import uuid
            from sqlalchemy import text

            async with db() as session, session.begin():
                await session.execute(
                    text("""
                        INSERT INTO debate_sessions
                            (id, goal_id, tenant_id, original_goal, consensus,
                             confidence, rounds, status, created_at, completed_at)
                        VALUES
                            (:id, :gid, :tid, :goal, :consensus,
                             :conf, :rounds, 'complete', NOW(), NOW())
                        ON CONFLICT (id) DO UPDATE
                            SET status='complete', completed_at=NOW()
                    """),
                    {
                        "id": session_id,
                        "gid": goal_id,
                        "tid": tenant_id,
                        "goal": original_goal,
                        "consensus": consensus,
                        "conf": confidence,
                        "rounds": rounds,
                    },
                )
                for p in proposals:
                    await session.execute(
                        text("""
                            INSERT INTO debate_proposals
                                (id, session_id, tenant_id, agent_role,
                                 proposal_text, critique_text, vote, round_number)
                            VALUES
                                (:id, :sid, :tid, :role,
                                 :prop, :crit, :vote, :rnd)
                            ON CONFLICT (id) DO NOTHING
                        """),
                        {
                            "id": p.get("id", uuid.uuid4().hex),
                            "sid": session_id,
                            "tid": tenant_id,
                            "role": p.get("role", "agent"),
                            "prop": p.get("proposal", "")[:2000],
                            "crit": p.get("critique", "")[:1000],
                            "vote": p.get("vote", "abstain"),
                            "rnd": p.get("round", 1),
                        },
                    )
        except Exception as exc:
            _logger.warning("debate_persist_failed: %s", exc)
