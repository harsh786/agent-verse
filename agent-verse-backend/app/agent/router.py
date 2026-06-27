"""Intent-based agent router — picks the best-fit agent for a goal."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.tenancy.context import TenantContext


@dataclass
class AgentScore:
    """Score breakdown for a single candidate agent."""

    agent_id: str
    agent_name: str
    score: float
    breakdown: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


@dataclass
class RoutingDecision:
    """Result of routing a goal to the best-fit agent."""

    agent_id: str | None
    reason: str
    confidence: float = 0.0
    mode: str = "single_agent"  # single_agent|multi_agent|needs_new_agent|needs_human_choice
    candidate_agents: list[dict] = field(default_factory=list)
    all_scores: list[AgentScore] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "reason": self.reason,
            "confidence": round(self.confidence, 3),
            "mode": self.mode,
            "candidate_agents": self.candidate_agents,
        }


class AgentRouter:
    """Route a natural-language goal to the most appropriate registered agent.

    Scoring is a weighted composite of three signals:
      - keyword overlap (40 %): match goal words against agent name + goal_template
      - connector match (40 %): match connector IDs against goal text
      - history (20 %): past success rate from the eval store (0.0 when unavailable)

    A routing decision is made only when the composite score ≥ 0.3.
    """

    def __init__(self, *, agent_store: Any, eval_store: Any = None, llm_provider: Any = None) -> None:
        self._agent_store = agent_store
        self._eval_store = eval_store
        self._llm_provider = llm_provider

    # ── scoring helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Split text into a lowercase word-token set."""
        return {w.lower() for w in re.findall(r"[a-z0-9]+", text.lower())}

    def _score_by_keywords(self, goal: str, agent: dict[str, Any]) -> float:
        """Jaccard-style overlap between goal words and agent name + goal_template."""
        goal_tokens = self._tokenize(goal)
        agent_text = f"{agent.get('name', '')} {agent.get('goal_template', '')}"
        agent_tokens = self._tokenize(agent_text)
        if not goal_tokens or not agent_tokens:
            return 0.0
        overlap = goal_tokens & agent_tokens
        return len(overlap) / max(len(goal_tokens), len(agent_tokens))

    def _score_by_connector_match(self, goal: str, agent: dict[str, Any]) -> float:
        """Fraction of connector IDs that appear as substrings in the goal."""
        connector_ids: list[str] = agent.get("connector_ids", []) or []
        if not connector_ids:
            return 0.0
        goal_lower = goal.lower()
        matched = sum(1 for cid in connector_ids if str(cid).lower() in goal_lower)
        return min(matched / len(connector_ids), 1.0)

    def _score_by_history(self, agent_id: str, tenant_ctx: TenantContext) -> float:
        """Return historical success rate, or 0.0 when eval store is unavailable."""
        if self._eval_store is None:
            return 0.0
        try:
            return float(self._eval_store.get_success_rate(agent_id, tenant_ctx=tenant_ctx))
        except Exception:
            return 0.0

    async def _score_by_llm(
        self,
        goal: str,
        agents: list[dict[str, Any]],
        provider: Any,
    ) -> list[AgentScore]:
        """Use LLM to classify goal and score agents by domain match."""
        if provider is None:
            return []
        try:
            from app.providers.base import CompletionRequest, Message
            agent_summaries = "\n".join([
                f"- {a.get('agent_id','?')}: {a.get('name','?')} — {a.get('goal_template','')[:100]}"
                for a in agents[:10]
            ])
            req = CompletionRequest(
                messages=[Message(
                    role="user",
                    content=(
                        f"Given this goal: '{goal}'\n\n"
                        f"Which of these agents is MOST capable?\n{agent_summaries}\n\n"
                        f"Return JSON: {{\"best_agent_id\": \"...\", \"confidence\": 0.0-1.0, "
                        f"\"reasoning\": \"...\"}}"
                    )
                )],
                model="",
            )
            resp = await provider.complete(req)
            import json
            m = re.search(r'\{[\s\S]*\}', resp.content)
            if m:
                data = json.loads(m.group())
                best_id = data.get("best_agent_id", "")
                conf = float(data.get("confidence", 0.5))
                scores = []
                for a in agents:
                    aid = a.get("agent_id", "")
                    scores.append(AgentScore(
                        agent_id=aid,
                        agent_name=a.get("name", ""),
                        score=conf if aid == best_id else max(0.0, conf - 0.4),
                        reasons=[f"LLM-classified: {data.get('reasoning', '')[:100]}"],
                    ))
                return scores
        except Exception:
            pass
        return []

    # ── public API ────────────────────────────────────────────────────────────

    async def route(
        self,
        goal: str,
        tenant_ctx: TenantContext,
        available_agents: list[dict[str, Any]] | None = None,
    ) -> RoutingDecision:
        """Pick the best-fit agent for *goal*.

        Parameters
        ----------
        goal:
            Natural-language goal text.
        tenant_ctx:
            Tenant context used for scoping agent lookup and history.
        available_agents:
            Pre-fetched list of agent dicts.  When *None* the router falls
            back to ``self._agent_store.list_all()``.

        Returns a :class:`RoutingDecision` with ``agent_id=None`` when no
        agent achieves a composite score ≥ 0.3.
        """
        if available_agents is None:
            agents = self._agent_store.list_all(tenant_ctx=tenant_ctx)
        else:
            agents = available_agents

        if not agents:
            return RoutingDecision(
                agent_id=None,
                reason="no_agents",
                confidence=0.0,
            )

        scores: list[AgentScore] = []
        for agent in agents:
            aid = agent.get("agent_id", "")
            kw = self._score_by_keywords(goal, agent)
            conn = self._score_by_connector_match(goal, agent)
            hist = self._score_by_history(aid, tenant_ctx)

            # Weighted composite: keywords 40 %, connector 40 %, history 20 %
            composite = kw * 0.4 + conn * 0.4 + hist * 0.2
            scores.append(
                AgentScore(
                    agent_id=aid,
                    agent_name=agent.get("name", ""),
                    score=composite,
                    breakdown={"keyword": kw, "connector": conn, "history": hist},
                )
            )

        # LLM scoring (optional, when provider available)
        if self._llm_provider and len(agents) > 1:
            try:
                llm_scores = await self._score_by_llm(goal, agents, self._llm_provider)
                if llm_scores:
                    # Blend: 60% LLM + 40% keyword
                    llm_by_id = {s.agent_id: s for s in llm_scores}
                    for s in scores:
                        llm_s = llm_by_id.get(s.agent_id)
                        if llm_s:
                            s.score = 0.4 * s.score + 0.6 * llm_s.score
                            s.reasons.extend(llm_s.reasons)
            except Exception:
                pass

        scores.sort(key=lambda s: s.score, reverse=True)
        best = scores[0]

        if best.score < 0.3:
            return RoutingDecision(
                agent_id=None,
                reason="low_confidence",
                confidence=best.score,
                all_scores=scores,
            )

        # Determine mode: needs_human_choice when top two agents are very close
        mode = "single_agent"
        candidate_agents: list[dict] = []
        if len(scores) >= 2:
            second_best = scores[1]
            if second_best.score >= 0.3 and (best.score - second_best.score) < 0.1:
                mode = "needs_human_choice"
                candidate_agents = [
                    {
                        "agent_id": s.agent_id,
                        "agent_name": s.agent_name,
                        "score": round(s.score, 3),
                    }
                    for s in scores
                    if s.score >= 0.3
                ]

        return RoutingDecision(
            agent_id=best.agent_id,
            reason="routed",
            confidence=best.score,
            mode=mode,
            candidate_agents=candidate_agents,
            all_scores=scores,
        )
