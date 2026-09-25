"""ProceduralMemoryStore — DB-backed storage for learned tool-use patterns (skills).

Procedural memory captures HOW TO DO things:
  - when goal type X is seen, tool sequence Y works well
  - success rates and usage counts track reliability

At planning time, the agent recalls relevant skills to suggest tool sequences.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.db.rls import sqlalchemy_rls_context

if TYPE_CHECKING:
    from app.agent.state import AgentState
    from app.tenancy.context import TenantContext


@dataclass
class Skill:
    """A learned tool-use pattern."""

    skill_id: str
    tenant_id: str
    goal_pattern: str  # Generalized goal description
    domain: str  # e.g., "jira", "github", "data", "analytics"
    tool_sequence: list[str]  # Ordered list of tools
    use_count: int = 1
    success_rate: float = 1.0
    avg_steps_saved: float = 0.0

    def to_hint(self) -> str:
        tools = " → ".join(self.tool_sequence[:5])
        return (
            f"[Skill: {self.goal_pattern[:60]}] "
            f"Tool sequence: {tools} "
            f"(success rate: {self.success_rate:.0%}, used {self.use_count}x)"
        )


def _extract_goal_pattern(goal: str) -> str:
    """Normalize goal text to a reusable pattern."""
    # Remove specific IDs, numbers, names
    pattern = re.sub(r"\b[A-Z][A-Z0-9]+-\d+\b", "TICKET", goal)  # Jira IDs
    pattern = re.sub(r"\b\d+\b", "N", pattern)
    pattern = re.sub(r'"[^"]{1,50}"', "VALUE", pattern)
    return pattern[:150].strip()


def _extract_domain(tools: list[str]) -> str:
    """Infer domain from tool names."""
    tool_str = " ".join(tools).lower()
    if "jira" in tool_str:
        return "jira"
    if "github" in tool_str or "gitlab" in tool_str:
        return "git"
    if "postgres" in tool_str or "sql" in tool_str:
        return "database"
    if "slack" in tool_str or "email" in tool_str:
        return "communication"
    if "web" in tool_str or "browser" in tool_str:
        return "web"
    return "general"


class ProceduralMemoryStore:
    """DB-backed procedural memory for cross-session skill learning."""

    def __init__(self, db_factory: Any = None) -> None:
        self._db = db_factory
        self._cache: dict[str, list[Skill]] = {}  # tenant_id → skills

    async def learn(
        self,
        *,
        state: AgentState,
        tenant_ctx: TenantContext,
        success: bool = True,
    ) -> None:
        """Learn a skill from a completed goal execution."""
        # Extract tool sequence
        tools: list[str] = []
        for step in state.steps:
            for tc in getattr(step, "tool_calls", None) or []:
                if isinstance(tc, dict):
                    tn = tc.get("tool_name", "")
                    if tn and tn not in tools:
                        tools.append(tn)
        if not tools:
            return  # Nothing to learn from goals without tools

        goal_pattern = _extract_goal_pattern(state.goal)
        domain = _extract_domain(tools)

        # Check if skill already exists in cache
        cached = self._cache.get(tenant_ctx.tenant_id, [])
        existing = next((s for s in cached if s.goal_pattern == goal_pattern), None)
        if existing:
            # Update existing skill
            total = existing.use_count + 1
            existing.success_rate = (
                existing.success_rate * existing.use_count + (1.0 if success else 0.0)
            ) / total
            existing.use_count = total
            skill = existing
        else:
            skill = Skill(
                skill_id=uuid.uuid4().hex,
                tenant_id=tenant_ctx.tenant_id,
                goal_pattern=goal_pattern,
                domain=domain,
                tool_sequence=tools[:8],
                use_count=1,
                success_rate=1.0 if success else 0.0,
            )
            self._cache.setdefault(tenant_ctx.tenant_id, []).append(skill)

        # DB persistence
        if self._db is not None:
            try:
                import json

                from sqlalchemy import text

                async with (
                    self._db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
                ):
                    await session.execute(
                        text("""
                        INSERT INTO procedural_memories
                            (id, tenant_id, goal_pattern, domain, tool_sequence,
                             use_count, success_rate, last_used_at, created_at)
                        VALUES
                            (:id, :tenant_id, :goal_pattern, :domain,
                             CAST(:tool_sequence AS jsonb), :use_count, :success_rate, NOW(), NOW())
                        ON CONFLICT DO NOTHING
                    """),
                        {
                            "id": skill.skill_id,
                            "tenant_id": tenant_ctx.tenant_id,
                            "goal_pattern": goal_pattern,
                            "domain": domain,
                            "tool_sequence": json.dumps(tools[:8]),
                            "use_count": skill.use_count,
                            "success_rate": skill.success_rate,
                        },
                    )
            except Exception as exc:
                try:
                    from app.observability.logging import get_logger

                    get_logger(__name__).warning("procedural_memory_persist_failed", error=str(exc))
                except Exception:
                    pass

    async def recall(
        self,
        *,
        goal: str,
        tenant_id: str,
        domain: str | None = None,
        min_success_rate: float = 0.6,
        limit: int = 3,
    ) -> list[Skill]:
        """Recall skills relevant to a goal."""
        if self._db is not None:
            try:
                return await self._recall_from_db(
                    goal=goal,
                    tenant_id=tenant_id,
                    domain=domain,
                    min_success_rate=min_success_rate,
                    limit=limit,
                )
            except Exception:
                pass

        # In-memory fallback
        skills = [
            s
            for s in self._cache.get(tenant_id, [])
            if s.success_rate >= min_success_rate and (domain is None or s.domain == domain)
        ]
        query_words = set(goal.lower().split())
        scored = [(sum(1 for w in query_words if w in s.goal_pattern.lower()), s) for s in skills]
        scored.sort(key=lambda x: (-x[0], -x[1].success_rate, -x[1].use_count))
        return [s for _, s in scored[:limit]]

    async def _recall_from_db(
        self, *, goal: str, tenant_id: str, domain: str | None, min_success_rate: float, limit: int
    ) -> list[Skill]:
        import json

        from sqlalchemy import text

        where_domain = "AND domain = :domain" if domain else ""
        async with (
            self._db() as session,
            sqlalchemy_rls_context(session, tenant_id),
        ):
            rows = (
                await session.execute(
                    text(f"""
                SELECT id, goal_pattern, domain, tool_sequence,
                       use_count, success_rate
                FROM procedural_memories
                WHERE tenant_id = :tenant_id
                  AND success_rate >= :min_rate
                  {where_domain}
                ORDER BY success_rate DESC, use_count DESC
                LIMIT :limit
            """),
                    {
                        "tenant_id": tenant_id,
                        "min_rate": min_success_rate,
                        "limit": limit * 3,
                        **({"domain": domain} if domain else {}),
                    },
                )
            ).fetchall()
        query_words = set(goal.lower().split())
        skills = []
        for row in rows:
            skill = Skill(
                skill_id=str(row[0]),
                tenant_id=tenant_id,
                goal_pattern=str(row[1]),
                domain=str(row[2]),
                tool_sequence=json.loads(row[3]) if row[3] else [],
                use_count=int(row[4]),
                success_rate=float(row[5]),
            )
            relevance = sum(1 for w in query_words if w in skill.goal_pattern.lower())
            skills.append((relevance, skill))
        skills.sort(key=lambda x: (-x[0], -x[1].success_rate))
        return [s for _, s in skills[:limit]]

    def format_for_context(self, skills: list[Skill]) -> str:
        """Format skills as a context block for planner prompt."""
        if not skills:
            return ""
        lines = ["[Procedural memory — relevant skills for this goal type:]"]
        for skill in skills:
            lines.append(f"  • {skill.to_hint()}")
        return "\n".join(lines)
