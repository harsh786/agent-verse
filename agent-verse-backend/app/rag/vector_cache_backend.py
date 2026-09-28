"""
Vector Cache Backends for SemanticCache L2.

Durable, cross-replica L2 next to the Redis scan.
Backends:
  - PgVectorCacheBackend: pgvector cosine search over `semantic_cache_entries`
    (write-through from SemanticCache.store_async; TTL-bounded; exact scan of
    the tenant's live window — the TEXT embedding column has no ANN index)
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
    """Durable, cross-replica semantic cache L2 on ``semantic_cache_entries``.

    Rows are written through by ``SemanticCache.store_async`` (previously nothing
    ever wrote this table, so every lookup here was a guaranteed miss) and
    cleared by ``SemanticCache.clear_async``. Every statement runs under the
    tenant's RLS context with an explicit ``tenant_id`` predicate. Lookups only
    consider rows younger than ``ttl_seconds`` (same TTL as the Redis L2, via
    ``idx_semantic_cache_tenant_created``) and stale rows are purged on write, so
    the per-tenant working set stays bounded.

    Note: ``embedding`` is a TEXT column cast to ``vector`` at query time (the
    dimension is not fixed by the schema), so this is an exact scan of the
    tenant's live window, not an HNSW ANN lookup.
    """

    # Purge a tenant's expired rows on roughly one write in this many.
    _PURGE_EVERY = 64

    def __init__(self, db_factory: Any, ttl_seconds: float = 3600.0) -> None:
        self._db = db_factory
        self._ttl = max(1, int(ttl_seconds))
        self._writes = 0

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
                        SELECT response, score FROM (
                            SELECT response,
                                   1 - (CAST(embedding AS vector) <=> CAST(:emb AS vector))
                                       AS score
                            FROM semantic_cache_entries
                            WHERE tenant_id = :tid
                              AND created_at > now() - make_interval(secs => :ttl)
                              AND vector_dims(CAST(embedding AS vector)) = :dim
                        ) AS candidates
                        WHERE score >= :threshold
                        ORDER BY score DESC
                        LIMIT 1
                    """),
                    # ``embedding`` is a TEXT column: it must be cast before the
                    # pgvector operator (the previous ``embedding <=> vector``
                    # raised UndefinedFunction on every call, swallowed at DEBUG),
                    # and rows of another dimension are skipped, not an error.
                    {
                        "emb": emb_str,
                        "tid": tenant_id,
                        "threshold": threshold,
                        "ttl": self._ttl,
                        "dim": len(embedding),
                    },
                )
                row = result.fetchone()
                if row:
                    return {"response": row[0], "score": float(row[1])}
        except Exception as e:
            # A cache miss is safe, but a broken backend must be visible.
            logger.warning("pgvector_cache_get_failed", error=str(e)[:120])
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
                        VALUES (:id, :tid, :q, CAST(:emb AS vector), :resp, NOW())
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
                self._writes += 1
                if self._writes % self._PURGE_EVERY == 0:
                    await session.execute(
                        text(
                            "DELETE FROM semantic_cache_entries WHERE tenant_id = :tid "
                            "AND created_at <= now() - make_interval(secs => :ttl)"
                        ),
                        {"tid": tenant_id, "ttl": self._ttl},
                    )
        except Exception as e:
            logger.warning("pgvector_cache_store_failed", error=str(e)[:120])

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
            logger.warning("pgvector_cache_clear_failed", error=str(e)[:120])

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
    ttl_seconds: float = 3600.0,
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
            return PgVectorCacheBackend(db_factory, ttl_seconds=ttl_seconds)
        except Exception as e:
            logger.debug("pgvector_backend_unavailable", error=str(e)[:60])

    logger.info("semantic_cache_backend_in_memory")
    return InMemoryCacheBackend()
