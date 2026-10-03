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
    #: Cosine similarity to the recall query, computed in SQL (MEM-40).
    similarity: float | None = None

    def to_context_snippet(self) -> str:
        """Format for injection into planner context."""
        return (
            f"[Past episode — {self.outcome}]\n"
            f"Goal: {self.goal_text[:100]}\n"
            f"Actions: {self.action_summary[:150]}\n"
            f"Lesson: {self.lessons[:200]}"
        )


#: Candidates each recall query returns (MEM-40: each is index-backed — HNSW on
#: ``embedding_vec``, GIN trigram on ``goal_text``, and the quality window).
_CANDIDATE_WINDOW = 50
#: Width of ``episodic_memories.embedding_vec`` (migration d46b0c2e4f85).
_EPISODIC_EMBEDDING_DIM = 2048
#: HNSW candidate list for the tenant-filtered similarity query.
_HNSW_EF_SEARCH = 200
#: Tenants with at most this many embedded episodes are searched exactly.
_EXACT_SEARCH_MAX_ROWS = 5_000
_ORDER_SUFFIX = "/*order*/"


def _fit_vector(vec: list[float]) -> list[float] | None:
    """Zero-pad a narrower embedding to the column width (cosine unchanged);
    a wider one cannot be stored without changing its geometry."""
    n = len(vec)
    if n == _EPISODIC_EMBEDDING_DIM:
        return list(vec)
    if 0 < n < _EPISODIC_EMBEDDING_DIM:
        return [*vec, *([0.0] * (_EPISODIC_EMBEDDING_DIM - n))]
    return None


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(str(float(v)) for v in vec) + "]"


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
        if ep.similarity is not None:
            return ep.similarity
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
        self._iterative_scan: bool | None = None
        # In-memory cache for current session (fast recall)
        self._cache: dict[str, list[Episode]] = {}  # tenant_id → episodes

    async def record(
        self,
        *,
        state: AgentState,
        tenant_ctx: TenantContext,
        quality_score: float = 0.5,
    ) -> bool:
        """Record a goal execution as an episode. Called on goal completion.

        Every text field passes the shared memory-write gate first (MEM-68): a
        PII/secret/prompt-injection payload is never stored, here or in the
        process cache. Returns False when the episode was LOST (gate outage or
        DB failure) so the caller can flag the goal memory-degraded; a guardrail
        block is a decision, not a loss, and returns True.
        """
        from app.agent.state import GoalStatus
        from app.memory.screening import MemoryScreeningError, screen_memory_fields

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

        try:
            screened = await screen_memory_fields(
                {
                    "goal_text": state.goal[:200],
                    "action_summary": action_summary,
                    "lessons": lessons,
                },
                tenant_id=tenant_ctx.tenant_id,
                goal_id=state.goal_id,
                store="episodic",
            )
        except MemoryScreeningError as exc:
            _log_degraded("record", tenant_ctx.tenant_id, exc)
            return False
        if screened is None:
            return True
        action_summary = screened["action_summary"]
        lessons = screened["lessons"]

        episode = Episode(
            episode_id=uuid.uuid4().hex,
            tenant_id=tenant_ctx.tenant_id,
            goal_id=state.goal_id,
            goal_text=screened["goal_text"],
            action_summary=action_summary,
            outcome=outcome,
            lessons=lessons,
            quality_score=quality_score,
            steps_count=len(state.steps),
            tools_used=tools_used[:10],
        )

        # Embed goal text for semantic recall
        embedding_model = ""
        if self._embedder is not None:
            try:
                from app.providers.base import EmbedRequest

                resp = await self._embedder.embed(EmbedRequest(texts=[episode.goal_text]))
                if resp.embeddings:
                    episode.embedding = list(resp.embeddings[0])
                    embedding_model = str(getattr(resp, "model", "") or "")
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
            fitted = _fit_vector(episode.embedding) if episode.embedding else None
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
                             outcome, lessons, embedding, embedding_vec, embedding_dim,
                             embedding_model, quality_score, steps_count, tools_used,
                             created_at)
                        VALUES
                            (:id, :tenant_id, :goal_id, :goal_text, :action_summary,
                             :outcome, :lessons, NULL, CAST(:vec AS vector), :dim,
                             :model, :quality_score, :steps_count,
                             CAST(:tools_used AS jsonb), NOW())
                    """),
                        {
                            "id": episode.episode_id,
                            "tenant_id": tenant_ctx.tenant_id,
                            "goal_id": state.goal_id,
                            "goal_text": episode.goal_text,
                            "action_summary": action_summary,
                            "outcome": outcome,
                            "lessons": lessons,
                            # MEM-40: the indexed vector column (the JSONB copy
                            # is no longer written — recall never reads it).
                            "vec": _vector_literal(fitted) if fitted else None,
                            "dim": len(episode.embedding or []) if fitted else None,
                            "model": embedding_model or None if fitted else None,
                            "quality_score": quality_score,
                            "steps_count": len(state.steps),
                            "tools_used": json.dumps(tools_used[:10]),
                        },
                    )
            except Exception as exc:
                _log_degraded("record", tenant_ctx.tenant_id, exc)
                return False
        return True

    @staticmethod
    async def _small_tenant(session: Any, tenant_id: str) -> bool:
        """Whether the tenant has few enough embedded episodes for exact search."""
        from sqlalchemy import text

        count = (
            await session.execute(
                text(
                    "SELECT count(*) FROM (SELECT 1 FROM episodic_memories "
                    "WHERE tenant_id = :t AND embedding_vec IS NOT NULL LIMIT :cap) AS x"
                ),
                {"t": tenant_id, "cap": _EXACT_SEARCH_MAX_ROWS + 1},
            )
        ).scalar()
        return int(count or 0) <= _EXACT_SEARCH_MAX_ROWS

    async def _iterative_scan_supported(self, session: Any) -> bool:
        """pgvector >= 0.8 (hnsw.iterative_scan); probed once per store."""
        if self._iterative_scan is None:
            from sqlalchemy import text

            version = (
                await session.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                )
            ).scalar()
            try:
                parts = tuple(int(p) for p in str(version or "0").split(".")[:2])
            except ValueError:
                parts = (0, 0)
            self._iterative_scan = parts >= (0, 8)
        return self._iterative_scan

    async def _embed_query(
        self, goal: str, tenant_id: str, degraded: list[str] | None
    ) -> tuple[list[float] | None, str]:
        """``(query vector, embedding model)``; ``(None, "")`` without one."""
        if self._embedder is None:
            return None, ""
        try:
            from app.providers.base import EmbedRequest

            resp = await self._embedder.embed(EmbedRequest(texts=[goal[:200]]))
            vec = _parse_embedding(resp.embeddings[0]) if resp.embeddings else None
            return vec, str(getattr(resp, "model", "") or "")
        except Exception as exc:
            _log_degraded("embed", tenant_id, exc)
            if degraded is not None:
                degraded.append("episodic_query_embed_failed")
            return None, ""

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
        query_vec, query_model = await self._embed_query(goal, tenant_id, degraded)
        if self._db is not None:
            try:
                return await self._recall_from_db(
                    goal=goal,
                    tenant_id=tenant_id,
                    limit=limit,
                    outcome_filter=outcome_filter,
                    query_vec=query_vec,
                    query_model=query_model,
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
        query_model: str = "",
    ) -> list[Episode]:
        from sqlalchemy import text

        where_outcome = "AND outcome = :outcome" if outcome_filter else ""
        base_cols = (
            "id, goal_id, goal_text, action_summary, outcome, lessons, quality_score, "
            "steps_count, tools_used"
        )
        base: dict[str, Any] = {
            "tenant_id": tenant_id,
            "window": max(limit * 3, _CANDIDATE_WINDOW),
            **({"outcome": outcome_filter} if outcome_filter else {}),
        }
        # MEM-40: every candidate query is index-backed. Semantic: HNSW over
        # embedding_vec::halfvec (same embedder dimension/model only). Lexical:
        # the pg_trgm ``%`` / ``<%`` operators (GIN trigram on goal_text) —
        # word_similarity over every row of the tenant used to run per call.
        # Quality window: (tenant_id, quality_score DESC, created_at DESC).
        queries: list[tuple[str, dict[str, Any]]] = []
        fitted = _fit_vector(query_vec) if query_vec is not None else None
        if fitted is not None and query_vec is not None:
            dim = _EPISODIC_EMBEDDING_DIM
            queries.append(
                (
                    f"SELECT {base_cols}, 1 - (embedding_vec::halfvec({dim}) "
                    f"<=> CAST(:qvec AS halfvec({dim}))) AS similarity "
                    f"FROM episodic_memories WHERE tenant_id = :tenant_id {where_outcome} "
                    "AND embedding_vec IS NOT NULL AND embedding_dim = :qdim "
                    "AND (embedding_model IS NULL OR :qmodel = '' OR embedding_model = :qmodel) "
                    f"ORDER BY (embedding_vec::halfvec({dim}) <=> CAST(:qvec AS halfvec({dim})))"
                    f"{_ORDER_SUFFIX} LIMIT :window",
                    {
                        **base,
                        "qvec": _vector_literal(fitted),
                        "qdim": len(query_vec),
                        "qmodel": query_model or "",
                    },
                )
            )
        if goal.strip():
            queries.append(
                (
                    f"SELECT {base_cols}, NULL AS similarity FROM episodic_memories "
                    f"WHERE tenant_id = :tenant_id {where_outcome} "
                    "AND (goal_text % :goal OR :goal <% goal_text) "
                    "ORDER BY word_similarity(:goal, goal_text) DESC, created_at DESC "
                    "LIMIT :window",
                    {**base, "goal": goal[:500]},
                )
            )
        queries.append(
            (
                f"SELECT {base_cols}, NULL AS similarity FROM episodic_memories "
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
            # Small tenant: exact cosine order (``+ 0`` keeps the planner off
            # the approximate HNSW index, which can miss an outlier). Large
            # tenant: HNSW with iterative scan — the scan is tenant-filtered, and
            # without it stops after ef_search global neighbours, so a tenant
            # whose episodes are not among them would get nothing back.
            exact = True
            if fitted is not None:
                exact = await self._small_tenant(session, tenant_id)
                if not exact and await self._iterative_scan_supported(session):
                    await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
                    await session.execute(text(f"SET LOCAL hnsw.ef_search = {_HNSW_EF_SEARCH}"))
            for sql, params in queries:
                sql = sql.replace(_ORDER_SUFFIX, " + 0" if exact else "")
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
                    similarity=(
                        float(row[9]) if len(row) > 9 and row[9] is not None else None
                    ),
                )
            )
        return _rank(episodes, goal, query_vec, limit)

    def format_for_context(self, episodes: list[Episode]) -> str:
        """Format episodes as a context block for planner prompt.

        Framed as untrusted data; an episode carrying an injection payload is
        dropped (MEM-68 read side).
        """
        from app.memory.prompt_framing import frame_memory_block

        return frame_memory_block(
            "Episodic memory — similar past experiences",
            [ep.to_context_snippet() + "\n" for ep in episodes],
        )
