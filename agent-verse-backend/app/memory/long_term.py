"""Long-term memory — cross-session learnings persisted across agent runs.

Stores domain knowledge extracted from successful goal completions that is
useful across different goals: tool preferences, common patterns, domain facts.

In production backed by PostgreSQL long_term_memory table.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger
from app.tenancy.context import TenantContext

# Guardrails 2.0 integration
try:
    from app.guardrails_v2.engine import guardrails_engine
    from app.guardrails_v2.models import GuardrailLayer

    _GUARDRAILS_AVAILABLE = True
except ImportError:  # pragma: no cover - guardrails_v2 always ships with the app
    _GUARDRAILS_AVAILABLE = False
    guardrails_engine = None  # type: ignore[assignment]
    GuardrailLayer = None  # type: ignore[assignment]

# Width of the long_term_memory.embedding column as currently sized by
# migration 0122 (app/db/migrations/versions/0122_ltm_embedding_2048.py). This
# is a single fixed-width pgvector column (no per-row/per-collection dimension
# like RAG's knowledge_chunks_* tables), so recall_async's SQL must match
# whatever the latest applied migration actually set the column to — NOT
# settings.embedding_dim, which only selects the live embedder and can drift
# from the column's real width if an operator changes it without also writing
# a new resize migration. Update this constant (and add a migration) together
# whenever the column is resized again.
_LTM_EMBEDDING_DIM = 2048


@dataclass
class LongTermMemory:
    """A single cross-session learning entry."""

    content: str
    source_goal_id: str
    memory_type: str  # "tool_preference" | "domain_fact" | "failure_pattern" | "success_pattern"
    confidence: float = 1.0
    memory_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    tags: list[str] = field(default_factory=list)


class LongTermMemoryStore:
    """Per-tenant store for cross-session learnings.

    Learnings are extracted from completed goals and used to bias future
    planning prompts.
    """

    def __init__(self) -> None:
        # tenant_id → list of LongTermMemory
        self._memories: dict[str, list[LongTermMemory]] = {}
        # Wired at startup by lifespan so async methods can use it without
        # callers having to pass db explicitly.
        self._db_factory: Any = None

    def store(self, *, memory: LongTermMemory, tenant_ctx: TenantContext) -> str:
        self._memories.setdefault(tenant_ctx.tenant_id, []).append(memory)
        return memory.memory_id

    def recall(
        self,
        *,
        query: str,
        tenant_ctx: TenantContext,
        top_k: int = 10,
        memory_type: str | None = None,
    ) -> list[LongTermMemory]:
        """Recall relevant memories using simple keyword matching."""
        memories = self._memories.get(tenant_ctx.tenant_id, [])
        if memory_type:
            memories = [m for m in memories if m.memory_type == memory_type]
        query_lower = query.lower()
        scored = [
            (m, sum(1 for word in query_lower.split() if word in m.content.lower()))
            for m in memories
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [m for m, _ in scored[:top_k]]

    def delete(self, *, memory_id: str, tenant_ctx: TenantContext) -> bool:
        memories = self._memories.get(tenant_ctx.tenant_id, [])
        for i, m in enumerate(memories):
            if m.memory_id == memory_id:
                memories.pop(i)
                return True
        return False

    def list_all(
        self,
        *,
        tenant_ctx: TenantContext,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[LongTermMemory]:
        """Return this tenant's cached memories, optionally windowed.

        ``limit``/``offset`` let callers page instead of materialising the whole
        list; ``limit=None`` preserves the original unbounded behaviour for
        internal callers.
        """
        items = self._memories.get(tenant_ctx.tenant_id, [])
        if offset:
            items = items[offset:]
        if limit is not None:
            items = items[:limit]
        return list(items)

    # ── DB-backed CRUD (durable; used by the chat /memories API) ──────────────
    # Mirrors store_async's persistence pattern; falls back to the in-memory
    # cache when no DB factory is wired (tests / no-DB dev).

    async def list_all_async(
        self,
        *,
        tenant_ctx: TenantContext,
        limit: int = 500,
        offset: int = 0,
    ) -> list[LongTermMemory]:
        """Return durable memories for a tenant, newest first.

        Bounded by ``limit`` (default 500) and offset-pageable so the query never
        walks an unbounded ``long_term_memory`` table. Falls back to the bounded
        in-memory cache when no DB factory is wired.
        """
        db = self._db_factory
        if db is None:
            return self.list_all(tenant_ctx=tenant_ctx, limit=limit, offset=offset)
        try:
            from sqlalchemy import text

            async with db() as session:
                rows = (
                    await session.execute(
                        text(
                            "SELECT id, content, memory_type, confidence, source_goal_id, tags "
                            "FROM long_term_memory WHERE tenant_id = :tid "
                            "ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
                        ),
                        {"tid": tenant_ctx.tenant_id, "limit": limit, "offset": offset},
                    )
                ).mappings().all()
            return [
                LongTermMemory(
                    content=r["content"],
                    source_goal_id=r["source_goal_id"] or "",
                    memory_type=r["memory_type"] or "domain_fact",
                    confidence=float(r["confidence"] or 1.0),
                    memory_id=str(r["id"]),
                    tags=list(r["tags"] or []),
                )
                for r in rows
            ]
        except Exception as exc:
            get_logger(__name__).warning("ltm_list_db_failed", error=str(exc))
            return self.list_all(tenant_ctx=tenant_ctx, limit=limit, offset=offset)

    async def delete_async(self, *, memory_id: str, tenant_ctx: TenantContext) -> bool:
        self.delete(memory_id=memory_id, tenant_ctx=tenant_ctx)  # keep cache in sync
        db = self._db_factory
        if db is None:
            return True
        try:
            from sqlalchemy import text

            async with db() as session, session.begin():
                res = await session.execute(
                    text("DELETE FROM long_term_memory WHERE id = :id AND tenant_id = :tid"),
                    {"id": memory_id, "tid": tenant_ctx.tenant_id},
                )
            return (res.rowcount or 0) > 0
        except Exception as exc:
            get_logger(__name__).warning("ltm_delete_db_failed", error=str(exc))
            return False

    async def update_content_async(
        self, *, memory_id: str, content: str, tenant_ctx: TenantContext
    ) -> LongTermMemory | None:
        # cache
        found: LongTermMemory | None = None
        for m in self._memories.get(tenant_ctx.tenant_id, []):
            if m.memory_id == memory_id:
                m.content = content
                found = m
                break
        db = self._db_factory
        if db is None:
            return found
        try:
            from sqlalchemy import text

            async with db() as session, session.begin():
                res = await session.execute(
                    text(
                        "UPDATE long_term_memory SET content = :c "
                        "WHERE id = :id AND tenant_id = :tid"
                    ),
                    {"c": content, "id": memory_id, "tid": tenant_ctx.tenant_id},
                )
            if (res.rowcount or 0) == 0:
                return None
            return found or LongTermMemory(
                content=content, source_goal_id="chat", memory_type="domain_fact",
                memory_id=memory_id,
            )
        except Exception as exc:
            get_logger(__name__).warning("ltm_update_db_failed", error=str(exc))
            return found

    async def create_user_memory_async(
        self, *, content: str, tenant_ctx: TenantContext, embedder: Any = None
    ) -> LongTermMemory:
        """Create a durable user-authored memory (from the chat /memories API)."""
        mem = LongTermMemory(content=content, source_goal_id="chat", memory_type="domain_fact")
        await self.store_async(
            memory=mem, tenant_ctx=tenant_ctx, db=self._db_factory, embedder=embedder
        )
        return mem

    async def delete_all_async(self, *, tenant_ctx: TenantContext) -> int:
        self._memories.pop(tenant_ctx.tenant_id, None)  # clear cache
        db = self._db_factory
        if db is None:
            return 0
        try:
            from sqlalchemy import text

            async with db() as session, session.begin():
                res = await session.execute(
                    text("DELETE FROM long_term_memory WHERE tenant_id = :tid"),
                    {"tid": tenant_ctx.tenant_id},
                )
            return res.rowcount or 0
        except Exception as exc:
            get_logger(__name__).warning("ltm_delete_all_db_failed", error=str(exc))
            return 0

    def extract_from_goal(
        self,
        *,
        goal: str,
        result: str,
        goal_id: str,
        tenant_ctx: TenantContext,
    ) -> list[str]:
        """Auto-extract learnings from a completed goal. Returns list of memory IDs."""
        memory = LongTermMemory(
            content=f"Goal: {goal[:200]} → Result: {result[:200]}",
            source_goal_id=goal_id,
            memory_type="success_pattern",
            confidence=0.8,
            tags=["auto-extracted"],
        )
        mid = self.store(memory=memory, tenant_ctx=tenant_ctx)
        return [mid]

    async def extract_from_goal_async(
        self,
        *,
        goal: str,
        result: str,
        tenant_ctx: TenantContext,
        db: Any = None,
        embedder: Any = None,
    ) -> LongTermMemory:
        """Extract a learning from a completed goal and persist it.

        Adds to the in-memory cache immediately (same-session recall) AND
        persists to DB via store_async so the learning survives restarts.
        """
        content = f"Goal: {goal[:200]} → Result: {result[:200]}"
        memory = LongTermMemory(
            content=content,
            source_goal_id="",
            memory_type="success_pattern",
            confidence=0.8,
            tags=["auto-extracted"],
        )
        # In-memory store (immediate availability for current session)
        self._memories.setdefault(tenant_ctx.tenant_id, []).append(memory)
        # Async DB persistence (survives restarts)
        await self.store_async(memory=memory, tenant_ctx=tenant_ctx, db=db, embedder=embedder)
        return memory

    async def store_async(
        self,
        *,
        memory: LongTermMemory,
        tenant_ctx: Any,
        db: Any = None,
        embedder: Any = None,
    ) -> str:
        """Async store — persists to DB with embedding if embedder available.

        Computes a vector embedding for semantic recall when an embedder is
        supplied, then upserts the row (with or without embedding) into
        ``long_term_memory``. Always updates the in-memory cache first so
        recall works even without a DB round-trip.
        """
        # Guardrails 2.0: MEMORY_WRITE layer — declared in GuardrailLayer but
        # never actually checked anywhere before this fix, so GDPR's "Block
        # PII in outputs" bundle rule and the baseline PII/secrets rule
        # (both of which already list "memory_write" in their ``layers``)
        # had zero real effect: unvetted content — RPA-scraped page text,
        # chat-authored memories, LLM-extracted goal summaries — flowed
        # straight into this per-tenant, cross-session, cross-restart store
        # with no gate at all. Single choke point: every write path
        # (``extract_from_goal_async``, ``store_rpa_extraction``,
        # ``create_user_memory_async``, and callers in
        # app/chat/memory_adapter.py + app/civilization/learning.py) funnels
        # through here. Mirrors the FINAL_OUTPUT block/redact pattern in
        # verifier_mixin.py — applied before the in-memory cache write (not
        # just the DB write) so a blocked/redacted memory never becomes
        # visible even for same-session recall.
        if _GUARDRAILS_AVAILABLE and guardrails_engine is not None and tenant_ctx is not None:
            try:
                guardrails_engine.ensure_default_rules(tenant_ctx.tenant_id)
                _g2_mem_result = await guardrails_engine.evaluate(
                    content=memory.content[:2000],
                    layer=GuardrailLayer.MEMORY_WRITE,
                    tenant_id=tenant_ctx.tenant_id,
                    goal_id=getattr(memory, "source_goal_id", None) or None,
                )
                if _g2_mem_result.get("blocked"):
                    memory.content = "[Content redacted by guardrail policy]"
                else:
                    _g2_mem_redacted = _g2_mem_result.get("redacted_content")
                    if _g2_mem_redacted and _g2_mem_redacted != memory.content[:2000]:
                        memory.content = _g2_mem_redacted + memory.content[2000:]
            except Exception as _g2_mem_exc:
                get_logger(__name__).warning(
                    "ltm_memory_write_guardrail_failed", error=str(_g2_mem_exc)
                )

        mid = self.store(memory=memory, tenant_ctx=tenant_ctx)
        if db is not None:
            try:
                import json as _json

                from sqlalchemy import text

                # Compute embedding when an embedder is provided
                embedding_str: str | None = None
                if embedder is not None:
                    try:
                        from app.providers.base import EmbedRequest

                        resp = await embedder.embed(EmbedRequest(texts=[memory.content]))
                        if resp.embeddings:
                            vec = resp.embeddings[0]
                            embedding_str = "[" + ",".join(str(v) for v in vec) + "]"
                    except Exception:
                        pass  # Embedding failure is non-fatal

                async with db() as session, session.begin():
                    if embedding_str is not None:
                        await session.execute(
                            text(
                                """INSERT INTO long_term_memory
                                    (id, tenant_id, content, memory_type, confidence,
                                     source_goal_id, tags, embedding)
                                    VALUES (:id, :tid, :content, :mtype, :conf, :sgid,
                                            :tags, CAST(:emb AS vector))
                                    ON CONFLICT (id) DO UPDATE SET
                                        embedding = EXCLUDED.embedding"""
                            ),
                            {
                                "id": mid,
                                "tid": tenant_ctx.tenant_id,
                                "content": memory.content,
                                "mtype": memory.memory_type,
                                "conf": getattr(memory, "confidence", 1.0),
                                "sgid": getattr(memory, "source_goal_id", ""),
                                "tags": _json.dumps(getattr(memory, "tags", [])),
                                "emb": embedding_str,
                            },
                        )
                    else:
                        await session.execute(
                            text(
                                """INSERT INTO long_term_memory
                                    (id, tenant_id, content, memory_type, confidence,
                                     source_goal_id, tags)
                                    VALUES (:id, :tid, :content, :mtype, :conf, :sgid, :tags)
                                    ON CONFLICT (id) DO NOTHING"""
                            ),
                            {
                                "id": mid,
                                "tid": tenant_ctx.tenant_id,
                                "content": memory.content,
                                "mtype": memory.memory_type,
                                "conf": getattr(memory, "confidence", 1.0),
                                "sgid": getattr(memory, "source_goal_id", ""),
                                "tags": _json.dumps(getattr(memory, "tags", [])),
                            },
                        )
            except Exception as exc:
                get_logger(__name__).warning("ltm_db_write_failed", error=str(exc))
        return mid

    async def store_rpa_extraction(
        self,
        *,
        url: str,
        extracted_text: str,
        goal_id: str,
        tenant_ctx: TenantContext,
        db: Any = None,
        embedder: Any = None,
        chunk_size: int = 500,
        source_type: str = "rpa_extraction",
    ) -> list[str]:
        """Store RPA-extracted page content into LTM.

        Short content (<50 chars) is ignored as noise.
        Content exceeding chunk_size is split into overlapping chunks so
        individual facts are retrievable via semantic search.

        Returns list of memory_ids created (empty if content was too short).
        """
        text = (extracted_text or "").strip()
        if len(text) < 50:
            return []

        # Determine tags
        if source_type == "rpa_vision":
            tags = ["rpa", "vision", "screenshot-analysis"]
        else:
            tags = ["rpa", "web-extraction"]

        # Split into chunks with 50-char overlap
        chunks: list[str] = []
        if len(text) <= chunk_size:
            chunks = [text]
        else:
            start = 0
            overlap = 50
            while start < len(text):
                end = start + chunk_size
                chunks.append(text[start:end])
                start += chunk_size - overlap
                if start >= len(text):
                    break

        memory_ids: list[str] = []
        total = len(chunks)
        for i, chunk in enumerate(chunks):
            if total > 1:
                content = f"[From {url} chunk {i + 1}/{total}] {chunk}"
            else:
                content = f"[From {url}] {chunk}"

            memory = LongTermMemory(
                content=content,
                source_goal_id=goal_id,
                memory_type="rpa_extraction",
                confidence=0.85,
                tags=tags,
            )
            mid = await self.store_async(
                memory=memory,
                tenant_ctx=tenant_ctx,
                db=db,
                embedder=embedder,
            )
            memory_ids.append(mid)

        return memory_ids

    async def recall_async(
        self,
        query: str,
        tenant_ctx: Any,
        top_k: int = 5,
        db: Any = None,
        embedder: Any = None,
    ) -> list[LongTermMemory]:
        """Recall memories using pgvector cosine similarity when embedder available.

        Falls back to keyword scoring when embedder/DB not available.
        This implements true semantic search — finds conceptually related memories
        even when exact words don't match.
        """
        # Try pgvector semantic search first
        if db is not None and embedder is not None:
            try:
                from sqlalchemy import text

                from app.db.rls import sqlalchemy_rls_context
                from app.providers.base import EmbedRequest

                # Embed the query
                resp = await embedder.embed(EmbedRequest(texts=[query]))
                if resp.embeddings:
                    query_vec = resp.embeddings[0]
                    vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"

                    # long_term_memory.embedding is a FIXED-width vector(_LTM_EMBEDDING_DIM)
                    # column, sized by migration 0122 — unlike the per-collection
                    # dynamic-width knowledge_chunks_* tables (app/rag/engine.py), there is
                    # no per-row/per-collection dimension to look up here, so the query must
                    # match whatever the *currently applied* migration actually sized the
                    # column to. Deliberately NOT settings.embedding_dim: that config value
                    # only chooses which embedder is wired up live and can drift from the
                    # column's real width whenever an operator edits it without also
                    # authoring/running a new LTM resize migration (that drift is exactly
                    # what migration 0122 itself fixed once already — see its docstring).
                    # If the column is ever resized again, update _LTM_EMBEDDING_DIM to match
                    # the new migration.
                    dim = _LTM_EMBEDDING_DIM
                    if dim > 2000:
                        vector_expr = f"embedding::halfvec({dim})"
                        qvec_expr = f"CAST(:qvec AS halfvec({dim}))"
                    else:
                        vector_expr = "embedding"
                        qvec_expr = "CAST(:qvec AS vector)"

                    async with (
                        db() as session,
                        sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
                    ):
                        result = await session.execute(
                            text(
                                f"""
                                SELECT id, content, memory_type, confidence,
                                       source_goal_id, tags, created_at,
                                       1 - ({vector_expr}
                                            <=> {qvec_expr}) AS similarity
                                FROM long_term_memory
                                WHERE tenant_id = :tid
                                  AND embedding IS NOT NULL
                                ORDER BY {vector_expr}
                                         <=> {qvec_expr}
                                LIMIT :k
                                """
                            ),
                            {
                                "qvec": vec_str,
                                "tid": tenant_ctx.tenant_id,
                                "k": top_k,
                            },
                        )
                        rows = result.fetchall()

                    if rows:
                        import json as _json

                        memories: list[LongTermMemory] = []
                        for row in rows:
                            try:
                                tags = _json.loads(row[5]) if row[5] else []
                            except Exception:
                                tags = []
                            m = LongTermMemory(
                                memory_id=row[0],
                                content=row[1],
                                memory_type=row[2],
                                confidence=float(row[3]) if row[3] else 1.0,
                                source_goal_id=row[4] or "",
                                tags=tags,
                                created_at=row[6].isoformat() if row[6] else "",
                            )
                            memories.append(m)
                            # Also populate in-memory cache
                            existing = self._memories.setdefault(tenant_ctx.tenant_id, [])
                            if not any(e.memory_id == m.memory_id for e in existing):
                                existing.append(m)
                        return memories
            except Exception as exc:
                from app.observability.logging import get_logger

                get_logger(__name__).warning("pgvector_recall_failed", error=str(exc))

        # Fallback: in-memory keyword search
        return self.recall(query=query, tenant_ctx=tenant_ctx, top_k=top_k)
