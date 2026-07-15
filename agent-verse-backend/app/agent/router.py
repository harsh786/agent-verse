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

    def __init__(
        self,
        *,
        agent_store: Any,
        eval_store: Any = None,
        llm_provider: Any = None,
        db_session_factory: Any = None,
    ) -> None:
        self._agent_store = agent_store
        self._eval_store = eval_store
        self._llm_provider = llm_provider
        self._db = db_session_factory

    # ── scoring helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Split text into a lowercase word-token set."""
        return {w.lower() for w in re.findall(r"[a-z0-9]+", text.lower())}

    # Systems whose names appear explicitly in goal text — used for anti-affinity.
    _SYSTEM_NAMES: dict[str, list[str]] = {
        "jira":       ["jira", "ticket", "sprint", "backlog", "epic", "assignee",
                       "assigned to me", "triage", "story point"],
        "confluence":  ["confluence"],
        "github":     ["github"],
        "gitlab":     ["gitlab"],
        "slack":      ["slack"],
        "linear":     ["linear"],
        "datadog":    ["datadog"],
        "sentry":     ["sentry"],
        "stripe":     ["stripe"],
        "hubspot":    ["hubspot"],
        "notion":     ["notion"],
        "postgres":   ["postgres", "postgresql"],
    }

    def _named_system_in_goal(self, goal_lower: str) -> str | None:
        """Return the system explicitly named in the goal, or None."""
        for system, aliases in self._SYSTEM_NAMES.items():
            if any(alias in goal_lower for alias in aliases):
                return system
        return None

    def _agent_primary_system(self, agent: dict[str, Any]) -> str | None:
        """Return the primary connector system for this agent, or None."""
        connector_ids: list[str] = agent.get("connector_ids", []) or []
        agent_name_lower = agent.get("name", "").lower()
        for system in self._SYSTEM_NAMES:
            if system in agent_name_lower:
                return system
            if any(system in cid.lower().replace("builtin-", "") for cid in connector_ids):
                return system
        return None

    def _score_by_keywords(self, goal: str, agent: dict[str, Any]) -> float:
        """Jaccard-style overlap between goal words and agent name + goal_template.

        Anti-affinity: when the goal explicitly names a system (e.g. "Jira") and
        this agent belongs to a *different* system (e.g. GitHub), the keyword
        score is zeroed out to prevent false positive routing.
        """
        goal_lower = goal.lower()
        named_system = self._named_system_in_goal(goal_lower)
        if named_system is not None:
            agent_system = self._agent_primary_system(agent)
            if agent_system is not None and agent_system != named_system:
                # Goal explicitly names a different system — hard exclude
                return 0.0

        goal_tokens = self._tokenize(goal)
        agent_text = f"{agent.get('name', '')} {agent.get('goal_template', '')}"
        agent_tokens = self._tokenize(agent_text)
        if not goal_tokens or not agent_tokens:
            return 0.0
        overlap = goal_tokens & agent_tokens
        return len(overlap) / max(len(goal_tokens), len(agent_tokens))

    def _score_by_connector_match(self, goal: str, agent: dict[str, Any]) -> float:
        """Bidirectional connector relevance score.

        Old approach: checked if the raw connector_id string appeared in the goal.
        Problem: connector_ids are often UUIDs (e.g. 'a56b384d...') or prefixed names
        like 'builtin-jira' — none of which appear verbatim in a goal like
        'Fetch latest Jira tickets'.

        New approach (bidirectional):
          1. Extract meaningful words from connector IDs by stripping 'builtin-'
             prefixes and splitting on '-' (e.g. 'builtin-jira' → 'jira',
             'a56b384d...' → raw UUID which we keep as-is but also check agent name).
          2. Check if any connector-derived word appears in the goal text.
          3. Also check if key goal words appear in any connector ID or agent connectors.

        Domain keyword map: maps common goal terms to connector names so that
        'jira', 'ticket', 'sprint', 'issue' → score boosted for jira-connected agents.
        """
        connector_ids: list[str] = agent.get("connector_ids", []) or []
        if not connector_ids:
            return 0.0

        goal_lower = goal.lower()
        goal_tokens = self._tokenize(goal)

        # Domain keyword → connector name mapping for semantic matching
        DOMAIN_KEYWORDS: dict[str, list[str]] = {
            "jira":       ["jira", "ticket", "issue", "sprint", "project", "backlog",
                           "epic", "story", "assignee", "assigned", "triage", "kanban"],
            "confluence": ["confluence", "wiki", "page", "document", "space", "knowledge"],
            "github":     ["github", "git", "repo", "repository", "pr", "pullrequest", "commit", "branch", "code", "file"],
            "gitlab":     ["gitlab", "merge", "pipeline", "ci", "cd"],
            "slack":      ["slack", "channel", "message", "notify", "post", "chat"],
            "linear":     ["linear", "issue", "cycle", "roadmap"],
            "datadog":    ["datadog", "monitor", "alert", "metric", "apm"],
            "sentry":     ["sentry", "error", "exception", "traceback", "crash"],
            "stripe":     ["stripe", "payment", "invoice", "charge", "customer", "subscription"],
            "hubspot":    ["hubspot", "crm", "contact", "deal", "lead", "pipeline"],
            "notion":     ["notion", "page", "block", "database"],
            "postgres":   ["postgres", "postgresql", "sql", "database", "query", "table"],
        }

        total_score = 0.0
        matched = 0

        for cid in connector_ids:
            cid_lower = cid.lower()

            # Strip common prefixes to get the core connector name
            core = cid_lower.replace("builtin-", "").replace("builtin_", "").split("/")[0]
            # e.g. 'builtin-jira' → 'jira', 'a56b384d...' → 'a56b384d...'

            # Direct: connector core name in goal
            if core in goal_lower:
                total_score += 1.0
                matched += 1
                continue

            # Semantic: goal keywords matching domain keywords for this connector
            domain_kws = DOMAIN_KEYWORDS.get(core, [])
            kw_hit = any(kw in goal_lower for kw in domain_kws)
            if kw_hit:
                total_score += 0.8
                matched += 1
                continue

            # Reverse: goal tokens in connector core (partial)
            if any(tok in core for tok in goal_tokens if len(tok) > 3):
                total_score += 0.5
                matched += 1
                continue

        # Normalize: score = avg hit weight across all connectors, capped at 1.0
        return min(total_score / len(connector_ids), 1.0)

    def _score_by_history(self, agent_id: str, tenant_ctx: TenantContext) -> float:
        """Return historical success rate from eval store, or 0.0 when unavailable."""
        if self._eval_store is None:
            return 0.0
        try:
            return float(self._eval_store.get_success_rate(agent_id, tenant_ctx=tenant_ctx))
        except Exception:
            return 0.0

    async def _score_by_history_db(self, agent: dict, goal: str, tenant_ctx: Any) -> float:
        """Score agent based on historical success rate from evaluations table."""
        if self._db is None:
            return 0.0
        agent_id = agent.get("agent_id", "")
        if not agent_id:
            return 0.0
        try:
            from sqlalchemy import text
            async with self._db() as session:
                row = (await session.execute(text("""
                    SELECT AVG(average_score) as avg_score, COUNT(*) as run_count
                    FROM evaluations e
                    JOIN goals g ON e.goal_id = g.id
                    WHERE g.agent_id = :aid AND g.tenant_id = :tid
                      AND e.created_at > NOW() - INTERVAL '30 days'
                """), {"aid": agent_id, "tid": tenant_ctx.tenant_id})).fetchone()
            if row and row[1] and row[1] > 0:
                return min(1.0, float(row[0] or 0))
        except Exception as exc:
            import logging; logging.getLogger(__name__).debug("router_history_score_failed: %s", exc)
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
            # Use DB-backed history scoring when available, fall back to eval store
            if self._db is not None:
                hist = await self._score_by_history_db(agent, goal, tenant_ctx)
            else:
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
