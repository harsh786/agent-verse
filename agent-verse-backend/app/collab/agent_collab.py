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

from dataclasses import dataclass, field
from typing import Any


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
