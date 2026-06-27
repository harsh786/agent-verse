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

from app.tenancy.context import TenantContext


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

    def list_all(self, *, tenant_ctx: TenantContext) -> list[LongTermMemory]:
        return list(self._memories.get(tenant_ctx.tenant_id, []))

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
        tenant_ctx: "TenantContext",
        db: Any = None,
        embedder: Any = None,
    ) -> "LongTermMemory":
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
        memory: "LongTermMemory",
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
                                            :tags, :emb::vector)
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
                from app.observability.logging import get_logger
                get_logger(__name__).warning("ltm_db_write_failed", error=str(exc))
        return mid

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
                from app.providers.base import EmbedRequest
                from sqlalchemy import text
                from app.db.rls import sqlalchemy_rls_context

                # Embed the query
                resp = await embedder.embed(EmbedRequest(texts=[query]))
                if resp.embeddings:
                    query_vec = resp.embeddings[0]
                    vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"

                    async with db() as session, sqlalchemy_rls_context(
                        session, tenant_ctx.tenant_id
                    ):
                        result = await session.execute(
                            text(
                                """
                                SELECT id, content, memory_type, confidence,
                                       source_goal_id, tags, created_at,
                                       1 - (embedding <=> :qvec::vector) AS similarity
                                FROM long_term_memory
                                WHERE tenant_id = :tid
                                  AND embedding IS NOT NULL
                                ORDER BY embedding <=> :qvec::vector
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
