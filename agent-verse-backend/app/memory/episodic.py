"""EpisodicMemoryStore — DB-backed storage for past goal experiences.

Episodic memory captures WHAT HAPPENED during past goal executions:
  - what the goal was
  - what actions were taken
  - what the outcome was
  - what lessons were learned

At planning time, the agent recalls similar past episodes to inform its approach.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent.state import AgentState
    from app.tenancy.context import TenantContext


@dataclass
class Episode:
    """A single past experience."""

    episode_id: str
    tenant_id: str
    goal_id: str
    goal_text: str
    action_summary: str
    outcome: str  # "success" | "failed" | "partial"
    lessons: str
    quality_score: float = 0.5
    steps_count: int = 0
    tools_used: list[str] = field(default_factory=list)
    embedding: list[float] | None = None

    def to_context_snippet(self) -> str:
        """Format for injection into planner context."""
        return (
            f"[Past episode — {self.outcome}]\n"
            f"Goal: {self.goal_text[:100]}\n"
            f"Actions: {self.action_summary[:150]}\n"
            f"Lesson: {self.lessons[:200]}"
        )


class EpisodicMemoryStore:
    """DB-backed episodic memory for cross-session experience recall."""

    def __init__(self, db_factory: Any = None, embedder: Any = None) -> None:
        self._db = db_factory
        self._embedder = embedder
        # In-memory cache for current session (fast recall)
        self._cache: dict[str, list[Episode]] = {}  # tenant_id → episodes

    async def record(
        self,
        *,
        state: AgentState,
        tenant_ctx: TenantContext,
        quality_score: float = 0.5,
    ) -> None:
        """Record a goal execution as an episode. Called on goal completion."""
        from app.agent.state import GoalStatus

        outcome = (
            "success"
            if state.status == GoalStatus.COMPLETE
            else "partial"
            if state.status == GoalStatus.WAITING_HUMAN
            else "failed"
        )
        # Extract action summary from steps
        action_parts = []
        for step in state.steps[:5]:
            desc = getattr(step, "description", "") or ""
            if desc:
                action_parts.append(desc[:80])
        action_summary = " → ".join(action_parts) if action_parts else "No steps recorded"

        # Extract tools used
        tools_used: list[str] = []
        for step in state.steps:
            for tc in getattr(step, "tool_calls", None) or []:
                if isinstance(tc, dict):
                    tn = tc.get("tool_name", "")
                    if tn and tn not in tools_used:
                        tools_used.append(tn)

        # Extract lesson from reflexion feedback
        lessons = (state.verification_feedback or "")[:300]

        episode = Episode(
            episode_id=uuid.uuid4().hex,
            tenant_id=tenant_ctx.tenant_id,
            goal_id=state.goal_id,
            goal_text=state.goal[:200],
            action_summary=action_summary,
            outcome=outcome,
            lessons=lessons,
            quality_score=quality_score,
            steps_count=len(state.steps),
            tools_used=tools_used[:10],
        )

        # Embed goal text for semantic recall
        if self._embedder is not None:
            try:
                from app.providers.base import EmbedRequest

                resp = await self._embedder.embed(EmbedRequest(texts=[state.goal[:200]]))
                if resp.embeddings:
                    episode.embedding = resp.embeddings[0]
            except Exception:
                pass

        # In-memory cache
        self._cache.setdefault(tenant_ctx.tenant_id, []).append(episode)
        if len(self._cache[tenant_ctx.tenant_id]) > 100:
            self._cache[tenant_ctx.tenant_id] = self._cache[tenant_ctx.tenant_id][-100:]

        # DB persistence
        if self._db is not None:
            try:
                import json

                from sqlalchemy import text

                async with self._db() as session, session.begin():
                    await session.execute(
                        text("""
                        INSERT INTO episodic_memories
                            (id, tenant_id, goal_id, goal_text, action_summary,
                             outcome, lessons, embedding, quality_score,
                             steps_count, tools_used, created_at)
                        VALUES
                            (:id, :tenant_id, :goal_id, :goal_text, :action_summary,
                             :outcome, :lessons, :embedding::jsonb, :quality_score,
                             :steps_count, :tools_used::jsonb, NOW())
                    """),
                        {
                            "id": episode.episode_id,
                            "tenant_id": tenant_ctx.tenant_id,
                            "goal_id": state.goal_id,
                            "goal_text": episode.goal_text,
                            "action_summary": action_summary,
                            "outcome": outcome,
                            "lessons": lessons,
                            "embedding": json.dumps(episode.embedding)
                            if episode.embedding
                            else "null",
                            "quality_score": quality_score,
                            "steps_count": len(state.steps),
                            "tools_used": json.dumps(tools_used[:10]),
                        },
                    )
            except Exception as exc:
                try:
                    from app.observability.logging import get_logger

                    get_logger(__name__).warning("episodic_memory_persist_failed", error=str(exc))
                except Exception:
                    pass

    async def recall(
        self,
        *,
        goal: str,
        tenant_id: str,
        limit: int = 3,
        outcome_filter: str | None = None,
    ) -> list[Episode]:
        """Recall similar past episodes for a given goal."""
        # Try DB first if available
        if self._db is not None:
            try:
                return await self._recall_from_db(
                    goal=goal,
                    tenant_id=tenant_id,
                    limit=limit,
                    outcome_filter=outcome_filter,
                )
            except Exception:
                pass

        # Fall back to in-memory cache (keyword match)
        episodes = self._cache.get(tenant_id, [])
        if outcome_filter:
            episodes = [e for e in episodes if e.outcome == outcome_filter]
        # Simple keyword relevance
        query_words = set(goal.lower().split())
        scored = [(sum(1 for w in query_words if w in e.goal_text.lower()), e) for e in episodes]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:limit]]

    async def _recall_from_db(
        self, *, goal: str, tenant_id: str, limit: int, outcome_filter: str | None
    ) -> list[Episode]:
        import json

        from sqlalchemy import text

        where_outcome = "AND outcome = :outcome" if outcome_filter else ""
        async with self._db() as session:
            rows = (
                await session.execute(
                    text(f"""
                SELECT id, goal_id, goal_text, action_summary, outcome,
                       lessons, quality_score, steps_count, tools_used
                FROM episodic_memories
                WHERE tenant_id = :tenant_id {where_outcome}
                ORDER BY quality_score DESC, created_at DESC
                LIMIT :limit
            """),
                    {
                        "tenant_id": tenant_id,
                        "limit": limit * 3,
                        **({"outcome": outcome_filter} if outcome_filter else {}),
                    },
                )
            ).fetchall()
        # Keyword filter
        query_words = set(goal.lower().split())
        episodes = []
        for row in rows:
            ep = Episode(
                episode_id=str(row[0]),
                tenant_id=tenant_id,
                goal_id=str(row[1]),
                goal_text=str(row[2]),
                action_summary=str(row[3]),
                outcome=str(row[4]),
                lessons=str(row[5]),
                quality_score=float(row[6]),
                steps_count=int(row[7]),
                tools_used=json.loads(row[8]) if row[8] else [],
            )
            relevance = sum(1 for w in query_words if w in ep.goal_text.lower())
            episodes.append((relevance, ep))
        episodes.sort(key=lambda x: (-x[0], -x[1].quality_score))
        return [e for _, e in episodes[:limit]]

    def format_for_context(self, episodes: list[Episode]) -> str:
        """Format episodes as a context block for planner prompt."""
        if not episodes:
            return ""
        lines = ["[Episodic memory — similar past experiences:]"]
        for ep in episodes:
            lines.append(ep.to_context_snippet())
            lines.append("")
        return "\n".join(lines).strip()
