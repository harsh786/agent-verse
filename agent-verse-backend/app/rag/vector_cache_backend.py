"""
Vector Cache Backends for SemanticCache L2.

Replaces the O(n) Python Redis scan with ANN lookup.
Backends:
  - PgVectorCacheBackend: pgvector HNSW on `semantic_cache_entries` table
  - InMemoryCacheBackend: fallback for tests / no DB

`select_cache_backend()` probes capabilities and returns the best available.
"""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable

from app.observability.logging import get_logger

logger = get_logger(__name__)

_SIMILARITY_THRESHOLD = 0.92


@runtime_checkable
class CacheBackend(Protocol):
    """Backend protocol for semantic cache L2 storage."""

    async def get_similar(
        self,
        embedding: list[float],
        tenant_id: str,
        threshold: float = _SIMILARITY_THRESHOLD,
    ) -> dict[str, Any] | None:
        """Return the most similar cached entry, or None."""
        ...

    async def store(
        self,
        query: str,
        embedding: list[float],
        response: str,
        tenant_id: str,
    ) -> None:
        """Store a new cache entry."""
        ...

    async def clear(self, tenant_id: str) -> None:
        """Clear all entries for a tenant."""
        ...

    async def stats(self, tenant_id: str) -> dict[str, Any]:
        """Return hit/miss stats."""
        ...


class InMemoryCacheBackend:
    """Fallback in-memory backend for tests and no-DB environments."""

    def __init__(self) -> None:
        self._store: dict[str, list[dict[str, Any]]] = {}  # tenant_id → entries
        self._hits: dict[str, int] = {}
        self._misses: dict[str, int] = {}

    async def get_similar(
        self,
        embedding: list[float],
        tenant_id: str,
        threshold: float = _SIMILARITY_THRESHOLD,
    ) -> dict[str, Any] | None:
        entries = self._store.get(tenant_id, [])
        best_score = 0.0
        best_entry = None
        for entry in entries:
            score = _cosine_similarity(embedding, entry["embedding"])
            if score > best_score:
                best_score = score
                best_entry = entry
        if best_score >= threshold and best_entry:
            self._hits[tenant_id] = self._hits.get(tenant_id, 0) + 1
            return {"response": best_entry["response"], "score": best_score}
        self._misses[tenant_id] = self._misses.get(tenant_id, 0) + 1
        return None

    async def store(
        self,
        query: str,
        embedding: list[float],
        response: str,
        tenant_id: str,
    ) -> None:
        if tenant_id not in self._store:
            self._store[tenant_id] = []
        self._store[tenant_id].append(
            {
                "query": query,
                "embedding": embedding,
                "response": response,
                "created_at": time.time(),
            }
        )

    async def clear(self, tenant_id: str) -> None:
        self._store.pop(tenant_id, None)

    async def stats(self, tenant_id: str) -> dict[str, Any]:
        total = self._hits.get(tenant_id, 0) + self._misses.get(tenant_id, 0)
        return {
            "hits": self._hits.get(tenant_id, 0),
            "misses": self._misses.get(tenant_id, 0),
            "hit_rate": self._hits.get(tenant_id, 0) / max(total, 1),
            "entries": len(self._store.get(tenant_id, [])),
            "backend": "in_memory",
        }


def _get_rls_imports() -> tuple[Any, Any]:
    """Lazy import of sqlalchemy text and rls context helper."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    return text, sqlalchemy_rls_context


class PgVectorCacheBackend:
    """pgvector HNSW-indexed semantic cache backend."""

    def __init__(self, db_factory: Any) -> None:
        self._db = db_factory

    async def get_similar(
        self,
        embedding: list[float],
        tenant_id: str,
        threshold: float = _SIMILARITY_THRESHOLD,
    ) -> dict[str, Any] | None:
        if self._db is None:
            return None
        try:
            text, sqlalchemy_rls_context = _get_rls_imports()
            emb_str = str(embedding)
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                result = await session.execute(
                    text("""
                        SELECT response,
                               1 - (embedding <=> :emb::vector) AS score
                        FROM semantic_cache_entries
                        WHERE tenant_id = :tid
                          AND 1 - (embedding <=> :emb::vector) >= :threshold
                        ORDER BY embedding <=> :emb::vector
                        LIMIT 1
                    """),
                    {"emb": emb_str, "tid": tenant_id, "threshold": threshold},
                )
                row = result.fetchone()
                if row:
                    return {"response": row[0], "score": float(row[1])}
        except Exception as e:
            logger.debug("pgvector_cache_get_failed", error=str(e)[:80])
        return None

    async def store(
        self,
        query: str,
        embedding: list[float],
        response: str,
        tenant_id: str,
    ) -> None:
        if self._db is None:
            return
        try:
            import uuid

            text, sqlalchemy_rls_context = _get_rls_imports()
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text("""
                        INSERT INTO semantic_cache_entries
                            (id, tenant_id, query, embedding, response, created_at)
                        VALUES (:id, :tid, :q, :emb::vector, :resp, NOW())
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "id": uuid.uuid4().hex,
                        "tid": tenant_id,
                        "q": query[:1000],
                        "emb": str(embedding),
                        "resp": response,
                    },
                )
        except Exception as e:
            logger.debug("pgvector_cache_store_failed", error=str(e)[:80])

    async def clear(self, tenant_id: str) -> None:
        if self._db is None:
            return
        try:
            text, sqlalchemy_rls_context = _get_rls_imports()
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text("DELETE FROM semantic_cache_entries WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                )
        except Exception as e:
            logger.debug("pgvector_cache_clear_failed", error=str(e)[:80])

    async def stats(self, tenant_id: str) -> dict[str, Any]:
        return {"backend": "pgvector", "tenant_id": tenant_id}


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = sum(x**2 for x in a) ** 0.5
    norm_b = sum(x**2 for x in b) ** 0.5
    denom = norm_a * norm_b
    return dot / denom if denom > 0 else 0.0


async def select_cache_backend(
    db_factory: Any = None,
    redis: Any = None,
) -> Any:
    """
    Probe available infrastructure and return the best cache backend.
    Priority: PgVector (HNSW) > InMemory
    """
    if db_factory is not None:
        try:
            from sqlalchemy import text

            async with db_factory() as session, session.begin():
                await session.execute(text("SELECT 1 FROM semantic_cache_entries LIMIT 1"))
            logger.info("semantic_cache_backend_pgvector")
            return PgVectorCacheBackend(db_factory)
        except Exception as e:
            logger.debug("pgvector_backend_unavailable", error=str(e)[:60])

    logger.info("semantic_cache_backend_in_memory")
    return InMemoryCacheBackend()
