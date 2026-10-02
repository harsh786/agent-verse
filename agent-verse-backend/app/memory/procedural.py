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


_CACHE_PER_TENANT = 200


class ProceduralMemoryUnavailableError(RuntimeError):
    """The durable procedural store could not be read or written."""


def _log_degraded(op: str, tenant_id: str, exc: BaseException) -> None:
    from app.observability.logging import get_logger
    from app.observability.metrics import record_memory_degraded

    record_memory_degraded("procedural", op)
    get_logger(__name__).warning(
        "procedural_memory_degraded",
        op=op,
        tenant_id=tenant_id,
        error=f"{type(exc).__name__}: {str(exc)[:200]}",
    )


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

        # MEM-68: the pattern is derived from goal text and recalled into later
        # planner prompts — it passes the shared memory-write gate first. A
        # block learns nothing; an outage raises (never stores unvetted text).
        from app.memory.screening import MemoryScreeningError, screen_memory_content

        try:
            goal_pattern_or_none = await screen_memory_content(
                _extract_goal_pattern(state.goal),
                tenant_id=tenant_ctx.tenant_id,
                goal_id=state.goal_id,
                store="procedural",
            )
        except MemoryScreeningError as exc:
            _log_degraded("learn", tenant_ctx.tenant_id, exc)
            raise ProceduralMemoryUnavailableError(str(exc)) from exc
        if goal_pattern_or_none is None:
            return
        goal_pattern = goal_pattern_or_none
        domain = _extract_domain(tools)
        outcome = 1.0 if success else 0.0

        # In-process cache (the DB-less build's store; with a DB it mirrors the
        # upserted row so this process's recall is current).
        cached = self._cache.setdefault(tenant_ctx.tenant_id, [])
        skill = next((s for s in cached if s.goal_pattern == goal_pattern), None)
        if skill is None:
            skill = Skill(
                skill_id=uuid.uuid4().hex,
                tenant_id=tenant_ctx.tenant_id,
                goal_pattern=goal_pattern,
                domain=domain,
                tool_sequence=tools[:8],
                use_count=0,
                success_rate=0.0,
            )
            cached.append(skill)
            if len(cached) > _CACHE_PER_TENANT:
                del cached[0]
        skill.success_rate = (skill.success_rate * skill.use_count + outcome) / (
            skill.use_count + 1
        )
        skill.use_count += 1
        if success:
            skill.tool_sequence = tools[:8]

        if self._db is None:
            return
        # One row per (tenant, pattern): every outcome — success or failure —
        # bumps use_count and folds into the running success rate. A failed run
        # keeps the stored tool sequence (the one that worked).
        import json

        from sqlalchemy import text

        try:
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                row = (
                    await session.execute(
                        text("""
                        INSERT INTO procedural_memories
                            (id, tenant_id, goal_pattern, domain, tool_sequence,
                             use_count, success_rate, last_used_at, created_at)
                        VALUES
                            (:id, :tenant_id, :goal_pattern, :domain,
                             CAST(:tool_sequence AS jsonb), 1, :outcome, NOW(), NOW())
                        ON CONFLICT (tenant_id, goal_pattern) DO UPDATE SET
                            success_rate = (procedural_memories.success_rate
                                            * procedural_memories.use_count + :outcome)
                                           / (procedural_memories.use_count + 1),
                            use_count = procedural_memories.use_count + 1,
                            tool_sequence = CASE WHEN :success
                                THEN CAST(:tool_sequence AS jsonb)
                                ELSE procedural_memories.tool_sequence END,
                            domain = CASE WHEN :success THEN :domain
                                ELSE procedural_memories.domain END,
                            last_used_at = NOW()
                        RETURNING id, use_count, success_rate
                    """),
                        {
                            "id": uuid.uuid4().hex,
                            "tenant_id": tenant_ctx.tenant_id,
                            "goal_pattern": goal_pattern,
                            "domain": domain,
                            "tool_sequence": json.dumps(tools[:8]),
                            "outcome": outcome,
                            "success": success,
                        },
                    )
                ).fetchone()
        except Exception as exc:
            _log_degraded("learn", tenant_ctx.tenant_id, exc)
            raise ProceduralMemoryUnavailableError(str(exc)) from exc
        if row is not None:
            skill.skill_id = str(row[0])
            skill.use_count = int(row[1])
            skill.success_rate = float(row[2])

    async def recall(
        self,
        *,
        goal: str,
        tenant_id: str,
        domain: str | None = None,
        min_success_rate: float = 0.6,
        limit: int = 3,
    ) -> list[Skill]:
        """Recall skills relevant to a goal.

        With a DB, candidates are filtered and ranked in SQL by trigram
        similarity to the goal's pattern (then success rate and use count)
        before the LIMIT — a tenant with many skills gets the matching ones, not
        an arbitrary top-by-rate window. A DB failure raises
        :class:`ProceduralMemoryUnavailableError` (logged + counted); it never
        silently falls back to this process's cache.
        """
        if self._db is not None:
            try:
                return await self._recall_from_db(
                    goal=goal,
                    tenant_id=tenant_id,
                    domain=domain,
                    min_success_rate=min_success_rate,
                    limit=limit,
                )
            except Exception as exc:
                _log_degraded("recall", tenant_id, exc)
                raise ProceduralMemoryUnavailableError(str(exc)) from exc

        skills = [
            s
            for s in self._cache.get(tenant_id, [])
            if s.success_rate >= min_success_rate and (domain is None or s.domain == domain)
        ]
        query_words = set(goal.lower().split())
        scored = [(sum(1 for w in query_words if w in s.goal_pattern.lower()), s) for s in skills]
        scored = [(rel, s) for rel, s in scored if rel > 0]
        scored.sort(key=lambda x: (-x[0], -x[1].success_rate, -x[1].use_count))
        return [s for _, s in scored[:limit]]

    async def _recall_from_db(
        self, *, goal: str, tenant_id: str, domain: str | None, min_success_rate: float, limit: int
    ) -> list[Skill]:
        import json

        from sqlalchemy import text

        where_domain = "AND domain = :domain" if domain else ""
        pattern = _extract_goal_pattern(goal)
        # Explicit transaction: the GUC set by sqlalchemy_rls_context is
        # transaction-local (SET LOCAL semantics), so it must share one
        # transaction with the SELECT rather than rely on autobegin ordering.
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            rows = (
                await session.execute(
                    text(f"""
                SELECT id, goal_pattern, domain, tool_sequence,
                       use_count, success_rate,
                       GREATEST(similarity(goal_pattern, :pattern),
                                word_similarity(:pattern, goal_pattern)) AS relevance
                FROM procedural_memories
                WHERE tenant_id = :tenant_id
                  AND success_rate >= :min_rate
                  AND (goal_pattern % :pattern OR :pattern <% goal_pattern)
                  {where_domain}
                ORDER BY relevance DESC, success_rate DESC, use_count DESC
                LIMIT :limit
            """),
                    {
                        "tenant_id": tenant_id,
                        "pattern": pattern,
                        "min_rate": min_success_rate,
                        "limit": limit,
                        **({"domain": domain} if domain else {}),
                    },
                )
            ).fetchall()
        skills: list[Skill] = []
        for row in rows:
            try:
                sequence = json.loads(row[3]) if isinstance(row[3], str) else row[3]
            except ValueError:
                # One corrupt row must not sink recall: skip it, visibly.
                from app.observability.logging import get_logger

                get_logger(__name__).warning(
                    "procedural_memory_corrupt_row", tenant_id=tenant_id, skill_id=str(row[0])
                )
                continue
            skills.append(
                Skill(
                    skill_id=str(row[0]),
                    tenant_id=tenant_id,
                    goal_pattern=str(row[1]),
                    domain=str(row[2]),
                    tool_sequence=list(sequence or []),
                    use_count=int(row[4]),
                    success_rate=float(row[5]),
                )
            )
        return skills

    def format_for_context(self, skills: list[Skill]) -> str:
        """Format skills as a context block for planner prompt.

        Framed as untrusted data; a skill carrying an injection payload is
        dropped (MEM-68 read side).
        """
        from app.memory.prompt_framing import frame_memory_block

        return frame_memory_block(
            "Procedural memory — relevant skills for this goal type",
            [f"  • {skill.to_hint()}" for skill in skills],
        )
