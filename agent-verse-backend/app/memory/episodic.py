"""EpisodicMemoryStore — DB-backed storage for past goal experiences.

Episodic memory captures WHAT HAPPENED during past goal executions:
  - what the goal was
  - what actions were taken
  - what the outcome was
  - what lessons were learned

At planning time, the agent recalls similar past episodes to inform its approach.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.db.rls import sqlalchemy_rls_context

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


#: Most-recent/best episodes scored per recall. ``episodic_memories.embedding``
#: is JSONB (migration 0089), not a pgvector column, so similarity is computed
#: in Python over this candidate window rather than by an ANN index.
_CANDIDATE_WINDOW = 200


def _parse_embedding(raw: Any) -> list[float] | None:
    """JSONB embedding → floats (asyncpg may hand JSONB back as a str)."""
    if raw is None:
        return None
    try:
        vec = json.loads(raw) if isinstance(raw, str | bytes) else raw
        if isinstance(vec, list) and vec:
            return [float(x) for x in vec]
    except (ValueError, TypeError):
        pass
    return None


def _cosine(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b):
        return None  # different embedding model/dimension — not comparable
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return None
    return dot / (na * nb)


def _rank(
    episodes: list[Episode], goal: str, query_vec: list[float] | None, limit: int
) -> list[Episode]:
    """Rank by embedding cosine when both sides have a comparable vector, else by
    keyword overlap (fraction of query words present), quality as tiebreak.

    Recall used to be keyword overlap only, although every episode's goal was
    embedded and the vector stored — semantically similar goals phrased with
    different words were never recalled.
    """
    words = set(goal.lower().split())

    def _relevance(ep: Episode) -> float:
        if query_vec is not None and ep.embedding:
            sim = _cosine(query_vec, ep.embedding)
            if sim is not None:
                return sim
        if not words:
            return 0.0
        return sum(1 for w in words if w in ep.goal_text.lower()) / len(words)

    scored = sorted(episodes, key=lambda e: (-_relevance(e), -e.quality_score))
    return scored[:limit]


class EpisodicMemoryUnavailableError(RuntimeError):
    """The durable episodic store could not be read."""


def _log_degraded(op: str, tenant_id: str, exc: BaseException) -> None:
    from app.observability.logging import get_logger
    from app.observability.metrics import record_memory_degraded

    record_memory_degraded("episodic", op)
    get_logger(__name__).warning(
        "episodic_memory_degraded",
        op=op,
        tenant_id=tenant_id,
        error=f"{type(exc).__name__}: {str(exc)[:200]}",
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
            except Exception as exc:
                # The episode is still stored, but only keyword recall can find
                # it — say so instead of swallowing the failure.
                _log_degraded("embed", tenant_ctx.tenant_id, exc)

        # In-memory cache
        self._cache.setdefault(tenant_ctx.tenant_id, []).append(episode)
        if len(self._cache[tenant_ctx.tenant_id]) > 100:
            self._cache[tenant_ctx.tenant_id] = self._cache[tenant_ctx.tenant_id][-100:]

        # DB persistence
        if self._db is not None:
            try:
                import json

                from sqlalchemy import text

                # episodic_memories is FORCE ROW LEVEL SECURITY: the INSERT must
                # carry the tenant GUC or a NOBYPASSRLS role rejects it (and the
                # except below would swallow that, silently losing the episode).
                async with (
                    self._db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
                ):
                    await session.execute(
                        text("""
                        INSERT INTO episodic_memories
                            (id, tenant_id, goal_id, goal_text, action_summary,
                             outcome, lessons, embedding, quality_score,
                             steps_count, tools_used, created_at)
                        VALUES
                            (:id, :tenant_id, :goal_id, :goal_text, :action_summary,
                             :outcome, :lessons, CAST(:embedding AS jsonb), :quality_score,
                             :steps_count, CAST(:tools_used AS jsonb), NOW())
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
                _log_degraded("record", tenant_ctx.tenant_id, exc)

    async def _embed_query(
        self, goal: str, tenant_id: str, degraded: list[str] | None
    ) -> list[float] | None:
        if self._embedder is None:
            return None
        try:
            from app.providers.base import EmbedRequest

            resp = await self._embedder.embed(EmbedRequest(texts=[goal[:200]]))
            return _parse_embedding(resp.embeddings[0]) if resp.embeddings else None
        except Exception as exc:
            _log_degraded("embed", tenant_id, exc)
            if degraded is not None:
                degraded.append("episodic_query_embed_failed")
            return None

    async def recall(
        self,
        *,
        goal: str,
        tenant_id: str,
        limit: int = 3,
        outcome_filter: str | None = None,
        degraded: list[str] | None = None,
    ) -> list[Episode]:
        """Recall similar past episodes for a given goal.

        Semantic (embedding cosine) when an embedder is wired and episodes carry
        a stored vector; keyword overlap otherwise. A failed query embedding is
        appended to ``degraded`` (keyword-only recall). A DB failure raises
        :class:`EpisodicMemoryUnavailableError` — it never silently answers from
        this process's cache.
        """
        query_vec = await self._embed_query(goal, tenant_id, degraded)
        if self._db is not None:
            try:
                return await self._recall_from_db(
                    goal=goal,
                    tenant_id=tenant_id,
                    limit=limit,
                    outcome_filter=outcome_filter,
                    query_vec=query_vec,
                )
            except Exception as exc:
                _log_degraded("recall", tenant_id, exc)
                raise EpisodicMemoryUnavailableError(str(exc)) from exc

        # DB-less build: the per-process store is the store.
        episodes = self._cache.get(tenant_id, [])
        if outcome_filter:
            episodes = [e for e in episodes if e.outcome == outcome_filter]
        return _rank(episodes, goal, query_vec, limit)

    async def _recall_from_db(
        self,
        *,
        goal: str,
        tenant_id: str,
        limit: int,
        outcome_filter: str | None,
        query_vec: list[float] | None = None,
    ) -> list[Episode]:
        from sqlalchemy import text

        where_outcome = "AND outcome = :outcome" if outcome_filter else ""
        columns = (
            "id, goal_id, goal_text, action_summary, outcome, lessons, quality_score, "
            "steps_count, tools_used, embedding"
        )
        base: dict[str, Any] = {
            "tenant_id": tenant_id,
            "window": max(limit * 3, _CANDIDATE_WINDOW),
            **({"outcome": outcome_filter} if outcome_filter else {}),
        }
        # MEM-13: candidates are selected by RELEVANCE in SQL (pgvector cosine
        # distance on the stored embedding when there is a query vector, and
        # pg_trgm word similarity on the goal text), plus the quality/recency
        # window. Relevance used to be a Python re-rank of the quality/recency
        # window alone, so an older or low-quality relevant episode was never
        # seen. Quality stays the tiebreak in ``_rank``.
        queries: list[tuple[str, dict[str, Any]]] = []
        if query_vec is not None:
            queries.append(
                (
                    f"SELECT {columns} FROM episodic_memories "
                    f"WHERE tenant_id = :tenant_id {where_outcome} "
                    "AND jsonb_typeof(embedding) = 'array' "
                    "AND jsonb_array_length(embedding) = :dim "
                    "ORDER BY CAST(CAST(embedding AS text) AS vector) "
                    "<=> CAST(:qvec AS vector) LIMIT :window",
                    {**base, "dim": len(query_vec), "qvec": json.dumps(query_vec)},
                )
            )
        if goal.strip():
            queries.append(
                (
                    f"SELECT {columns} FROM episodic_memories "
                    f"WHERE tenant_id = :tenant_id {where_outcome} "
                    "ORDER BY word_similarity(:goal, goal_text) DESC, created_at DESC "
                    "LIMIT :window",
                    {**base, "goal": goal[:500]},
                )
            )
        queries.append(
            (
                f"SELECT {columns} FROM episodic_memories "
                f"WHERE tenant_id = :tenant_id {where_outcome} "
                "ORDER BY quality_score DESC, created_at DESC LIMIT :window",
                base,
            )
        )
        # Tenant GUC for RLS plus the explicit tenant_id predicate (defense in
        # depth). Without the GUC a NOBYPASSRLS role sees zero rows.
        seen: dict[str, Any] = {}
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            for sql, params in queries:
                for row in (await session.execute(text(sql), params)).fetchall():
                    seen.setdefault(str(row[0]), row)
        rows = list(seen.values())
        episodes: list[Episode] = []
        for row in rows:
            try:
                tools = json.loads(row[8]) if isinstance(row[8], str) else (row[8] or [])
            except ValueError:
                # One corrupt row must not sink recall: skip it, visibly.
                from app.observability.logging import get_logger

                get_logger(__name__).warning(
                    "episodic_memory_corrupt_row", tenant_id=tenant_id, episode_id=str(row[0])
                )
                continue
            episodes.append(
                Episode(
                    episode_id=str(row[0]),
                    tenant_id=tenant_id,
                    goal_id=str(row[1]),
                    goal_text=str(row[2]),
                    action_summary=str(row[3]),
                    outcome=str(row[4]),
                    lessons=str(row[5]),
                    quality_score=float(row[6]),
                    steps_count=int(row[7]),
                    tools_used=list(tools),
                    embedding=_parse_embedding(row[9]) if len(row) > 9 else None,
                )
            )
        return _rank(episodes, goal, query_vec, limit)

    def format_for_context(self, episodes: list[Episode]) -> str:
        """Format episodes as a context block for planner prompt."""
        if not episodes:
            return ""
        lines = ["[Episodic memory — similar past experiences:]"]
        for ep in episodes:
            lines.append(ep.to_context_snippet())
            lines.append("")
        return "\n".join(lines).strip()
