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


def _fit_ltm_vector(vec: list[float]) -> list[float] | None:
    """Fit an embedding into the fixed-width column, or None if it cannot fit.

    A narrower vector (e.g. a 1024-d Qwen3-Embedding) is zero-padded: padding
    leaves dot products and norms — hence cosine similarity — unchanged, so it
    ranks exactly as it would in a column of its own width and still uses the
    halfvec HNSW index. Every such write used to fail with "expected 2048
    dimensions, not 1024", i.e. long-term memory was dead for any deployment
    whose embedder is not 2048-d. A wider vector cannot be shrunk without
    changing its geometry, so it is stored without a vector (text-recall only).
    Rows record their embedding model so recall compares like with like.
    """
    n = len(vec)
    if n == _LTM_EMBEDDING_DIM:
        return list(vec)
    if 0 < n < _LTM_EMBEDDING_DIM:
        return [*vec, *([0.0] * (_LTM_EMBEDDING_DIM - n))]
    return None


class LongTermMemoryUnavailableError(RuntimeError):
    """The durable long-term memory store could not be read or written."""


class LongTermMemoryBlockedError(ValueError):
    """The MEMORY_WRITE guardrail rejected the content; nothing was stored."""


async def screen_user_memory_content(content: str, *, tenant_id: str) -> str:
    """MEMORY_WRITE gate for a user-authored memory create/edit (full text).

    Returns the content to store (a redacting rule's output when one fired).
    Raises :class:`LongTermMemoryBlockedError` on a block and
    :class:`LongTermMemoryUnavailableError` when the guardrail cannot vet the
    content (fail closed). Shared by every edit route (MEM-03).
    """
    if not _GUARDRAILS_AVAILABLE or guardrails_engine is None:
        raise LongTermMemoryUnavailableError("memory-write guardrail is not available")
    try:
        guardrails_engine.ensure_default_rules(tenant_id)
        result = await guardrails_engine.evaluate(
            content=content, layer=GuardrailLayer.MEMORY_WRITE, tenant_id=tenant_id
        )
    except Exception as exc:
        get_logger(__name__).warning("ltm_memory_write_guardrail_failed", error=str(exc)[:200])
        raise LongTermMemoryUnavailableError(
            "memory-write guardrail could not vet the content; nothing was stored"
        ) from exc
    if result.get("blocked"):
        raise LongTermMemoryBlockedError("memory content rejected by the memory-write guardrail")
    redacted = result.get("redacted_content")
    return redacted if isinstance(redacted, str) and redacted else content


async def embed_ltm_content(embedder: Any, content: str) -> tuple[str, str, int] | None:
    """``(pgvector literal, model, raw dim)`` for *content*, or None.

    Non-fatal (the row is still found by keyword recall) but never silent.
    """
    if embedder is None:
        return None
    try:
        from app.providers.base import EmbedRequest

        resp = await embedder.embed(EmbedRequest(texts=[content]))
        raw_vec = resp.embeddings[0] if resp.embeddings else None
        fitted = _fit_ltm_vector(raw_vec) if raw_vec else None
        if raw_vec is None or fitted is None:
            return None
        literal = "[" + ",".join(str(v) for v in fitted) + "]"
        return literal, str(getattr(resp, "model", "") or ""), len(raw_vec)
    except Exception as exc:
        get_logger(__name__).warning("ltm_embedding_failed", error=str(exc)[:200])
        return None


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
        # Redis used to publish ``memory.created`` for MemoryTriggerConsumer
        # (bound by the lifespan; None → nothing published).
        self._event_redis: Any = None

    def set_event_redis(self, redis: Any) -> None:
        self._event_redis = redis

    async def _publish_created(self, memory: LongTermMemory, tenant_ctx: Any) -> None:
        """Publish ``memory.created``. MemoryTriggerConsumer subscribed to it but
        no code published it, so MEMORY_CREATED triggers could never fire."""
        if self._event_redis is None or tenant_ctx is None:
            return
        import json as _json

        plan = getattr(getattr(tenant_ctx, "plan", None), "value", None) or "free"
        try:
            from app.triggers.bus import publish_trigger_event

            # Stream XADD (+ legacy pub/sub while dual publish is on), TRG-18.
            await publish_trigger_event(
                self._event_redis,
                "memory.created",
                _json.dumps(
                    {
                        "tenant_id": tenant_ctx.tenant_id,
                        "tenant_plan": plan,
                        "memory_id": memory.memory_id,
                        "memory_type": memory.memory_type,
                        "source_goal_id": memory.source_goal_id,
                    }
                ),
            )
        except Exception as exc:
            get_logger(__name__).warning("memory_created_publish_failed", error=str(exc))

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
    # Falls back to the in-memory cache only when no DB factory is wired (tests /
    # no-DB dev). long_term_memory is FORCE RLS and the API role is NOBYPASSRLS,
    # so every statement MUST run inside sqlalchemy_rls_context — without the
    # tenant GUC, lists came back empty and GDPR delete-all matched 0 rows.
    # DB failures raise LongTermMemoryUnavailableError (-> 503) rather than
    # returning a fake empty list / False / 0 that looks like success.

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

            from app.db.rls import sqlalchemy_rls_context

            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                rows = (
                    await session.execute(
                        text(
                            "SELECT id, content, memory_type, confidence, source_goal_id, tags "
                            "FROM long_term_memory WHERE tenant_id = :tid "
                            "ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
                        ),
                        {"tid": tenant_ctx.tenant_id, "limit": limit, "offset": offset},
                    )
                ).fetchall()
            return [
                LongTermMemory(
                    content=r[1],
                    source_goal_id=r[4] or "",
                    memory_type=r[2] or "domain_fact",
                    confidence=float(r[3] or 1.0),
                    memory_id=str(r[0]),
                    tags=list(r[5] or []),
                )
                for r in rows
            ]
        except Exception as exc:
            get_logger(__name__).warning("ltm_list_db_failed", error=str(exc))
            raise LongTermMemoryUnavailableError("long-term memory list failed") from exc

    async def delete_async(self, *, memory_id: str, tenant_ctx: TenantContext) -> bool:
        db = self._db_factory
        if db is None:
            return self.delete(memory_id=memory_id, tenant_ctx=tenant_ctx)
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                res = await session.execute(
                    text("DELETE FROM long_term_memory WHERE id = :id AND tenant_id = :tid"),
                    {"id": memory_id, "tid": tenant_ctx.tenant_id},
                )
        except Exception as exc:
            get_logger(__name__).warning("ltm_delete_db_failed", error=str(exc))
            raise LongTermMemoryUnavailableError("long-term memory delete failed") from exc
        self.delete(memory_id=memory_id, tenant_ctx=tenant_ctx)  # keep cache in sync
        return (res.rowcount or 0) > 0

    async def update_content_async(
        self,
        *,
        memory_id: str,
        content: str,
        tenant_ctx: TenantContext,
        embedder: Any = None,
    ) -> LongTermMemory | None:
        """Edit a memory's content: screened (MEMORY_WRITE) and re-embedded.

        Raises :class:`LongTermMemoryBlockedError` when the guardrail blocks the
        new content and :class:`LongTermMemoryUnavailableError` when it cannot
        vet it or the DB write fails; nothing is changed in either case. The
        row's vector is replaced (or cleared without an embedder) so semantic
        recall never matches the old text.
        """
        content = await screen_user_memory_content(content, tenant_id=tenant_ctx.tenant_id)
        db = self._db_factory
        found: LongTermMemory | None = None
        for m in self._memories.get(tenant_ctx.tenant_id, []):
            if m.memory_id == memory_id:
                found = m
                break
        if db is None:
            if found is not None:
                found.content = content
            return found
        embedded = await embed_ltm_content(embedder, content)
        params: dict[str, Any] = {"c": content, "id": memory_id, "tid": tenant_ctx.tenant_id}
        if embedded is not None:
            vec_sql = (
                "embedding = CAST(:emb AS vector), embedding_model = :emodel, "
                "embedding_dim = :edim"
            )
            params["emb"], params["emodel"], params["edim"] = embedded
        else:
            vec_sql = "embedding = NULL, embedding_model = NULL, embedding_dim = NULL"
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                res = await session.execute(
                    text(
                        f"UPDATE long_term_memory SET content = :c, {vec_sql} "
                        "WHERE id = :id AND tenant_id = :tid"
                    ),
                    params,
                )
        except Exception as exc:
            get_logger(__name__).warning("ltm_update_db_failed", error=str(exc))
            raise LongTermMemoryUnavailableError("long-term memory update failed") from exc
        if (res.rowcount or 0) == 0:
            return None
        if found is not None:
            found.content = content
            return found
        return LongTermMemory(
            content=content, source_goal_id="chat", memory_type="domain_fact",
            memory_id=memory_id,
        )

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
        db = self._db_factory
        if db is None:
            cached = self._memories.pop(tenant_ctx.tenant_id, None) or []
            return len(cached)
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                res = await session.execute(
                    text("DELETE FROM long_term_memory WHERE tenant_id = :tid"),
                    {"tid": tenant_ctx.tenant_id},
                )
        except Exception as exc:
            get_logger(__name__).warning("ltm_delete_all_db_failed", error=str(exc))
            raise LongTermMemoryUnavailableError("long-term memory erasure failed") from exc
        self._memories.pop(tenant_ctx.tenant_id, None)  # clear cache
        return int(res.rowcount or 0)

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
        # store_async caches it in-memory (after the guardrail vets it) and
        # persists it; appending here first cached unvetted content and
        # duplicated the entry.
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
                # Fail closed: content the MEMORY_WRITE guardrail could not vet is
                # never stored (it used to be written unvetted).
                get_logger(__name__).warning(
                    "ltm_memory_write_guardrail_failed", error=str(_g2_mem_exc)
                )
                raise LongTermMemoryUnavailableError(
                    "memory-write guardrail could not vet the content; nothing was stored"
                ) from _g2_mem_exc

        mid = self.store(memory=memory, tenant_ctx=tenant_ctx)
        if db is not None:
            try:
                import json as _json

                from sqlalchemy import text

                from app.db.rls import sqlalchemy_rls_context

                # Compute embedding when an embedder is provided
                embedding_str: str | None = None
                embedding_model: str | None = None
                embedding_dim: int | None = None
                if embedder is not None:
                    try:
                        from app.providers.base import EmbedRequest

                        resp = await embedder.embed(EmbedRequest(texts=[memory.content]))
                        if resp.embeddings:
                            raw_vec = resp.embeddings[0]
                            fitted = _fit_ltm_vector(raw_vec)
                            if fitted is not None:
                                embedding_str = "[" + ",".join(str(v) for v in fitted) + "]"
                                embedding_model = str(getattr(resp, "model", "") or "")
                                embedding_dim = len(raw_vec)
                    except Exception as _emb_exc:
                        # Non-fatal (the row is stored without a vector and is
                        # still found by keyword recall) — but not silent.
                        get_logger(__name__).warning(
                            "ltm_embedding_failed", error=str(_emb_exc)[:200]
                        )

                # Under RLS like every other tenant write: without the tenant GUC a
                # least-privilege role rejects the INSERT.
                async with (
                    db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
                ):
                    if embedding_str is not None:
                        await session.execute(
                            text(
                                """INSERT INTO long_term_memory
                                    (id, tenant_id, content, memory_type, confidence,
                                     source_goal_id, tags, embedding, embedding_model,
                                     embedding_dim)
                                    VALUES (:id, :tid, :content, :mtype, :conf, :sgid,
                                            :tags, CAST(:emb AS vector), :emodel, :edim)
                                    ON CONFLICT (id) DO UPDATE SET
                                        embedding = EXCLUDED.embedding,
                                        embedding_model = EXCLUDED.embedding_model,
                                        embedding_dim = EXCLUDED.embedding_dim"""
                            ),
                            {
                                "emodel": embedding_model,
                                "edim": embedding_dim,
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
                # Fail honestly: the durable write failed, so do not leave the
                # memory in this process's cache (where it would look stored but
                # vanish on restart / be invisible to other replicas) and do not
                # return an id as if it were persisted.
                get_logger(__name__).warning("ltm_db_write_failed", error=str(exc))
                cached = self._memories.get(tenant_ctx.tenant_id, [])
                self._memories[tenant_ctx.tenant_id] = [
                    m for m in cached if m.memory_id != mid
                ]
                raise LongTermMemoryUnavailableError(
                    "long-term memory write failed; nothing was stored"
                ) from exc
        # MEM-05: announce the memory only once it exists — after the INSERT's
        # transaction committed (or the cache write when no DB is wired).
        await self._publish_created(memory, tenant_ctx)
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
                query_vec = _fit_ltm_vector(resp.embeddings[0]) if resp.embeddings else None
                if query_vec is not None:
                    query_dim = len(resp.embeddings[0])
                    query_model = str(getattr(resp, "model", "") or "")
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
                                  AND (embedding_model = :qmodel
                                       OR (embedding_model IS NULL AND :qdim = 2048))
                                ORDER BY {vector_expr}
                                         <=> {qvec_expr}
                                LIMIT :k
                                """
                            ),
                            {
                                "qvec": vec_str,
                                "tid": tenant_ctx.tenant_id,
                                "k": top_k,
                                "qmodel": query_model,
                                "qdim": query_dim,
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

        # Fallback: keyword search in the tenant's persisted memories. It used to
        # search only this process's cache — memories written on another replica
        # (or before a restart) were invisible whenever vector recall was
        # unavailable. The cache is used only when there is no database at all.
        if db is not None:
            persisted = await self._db_keyword_recall(query, tenant_ctx, top_k, db)
            if persisted is not None:
                return persisted
        return self.recall(query=query, tenant_ctx=tenant_ctx, top_k=top_k)

    async def _db_keyword_recall(
        self, query: str, tenant_ctx: Any, top_k: int, db: Any
    ) -> list[LongTermMemory] | None:
        import json as _json
        import re as _re

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        terms = _re.findall(r"\w{3,}", query.lower())[:8]
        if not terms:
            return []
        clauses = " OR ".join(f"content ILIKE :t{i}" for i in range(len(terms)))
        score = " + ".join(f"(content ILIKE :t{i})::int" for i in range(len(terms)))
        params: dict[str, Any] = {f"t{i}": f"%{t}%" for i, t in enumerate(terms)}
        try:
            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                rows = (
                    await session.execute(
                        text(
                            "SELECT id, content, memory_type, confidence, source_goal_id, "
                            f"tags, created_at FROM long_term_memory WHERE tenant_id = :tid "
                            f"AND ({clauses}) ORDER BY ({score}) DESC, created_at DESC LIMIT :k"
                        ),
                        {**params, "tid": tenant_ctx.tenant_id, "k": top_k},
                    )
                ).fetchall()
        except Exception as exc:
            get_logger(__name__).warning("ltm_keyword_recall_failed", error=str(exc))
            return None
        out: list[LongTermMemory] = []
        for row in rows:
            tags = row[5]
            if isinstance(tags, str):
                try:
                    tags = _json.loads(tags)
                except ValueError:
                    tags = []
            out.append(
                LongTermMemory(
                    memory_id=row[0], content=row[1], memory_type=row[2],
                    confidence=float(row[3]) if row[3] is not None else 1.0,
                    source_goal_id=row[4] or "", tags=list(tags or []),
                    created_at=row[6].isoformat() if row[6] else "",
                )
            )
        return out
