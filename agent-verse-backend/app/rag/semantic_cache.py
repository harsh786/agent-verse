"""Semantic cache with TTL support.

Entries expire after `ttl_seconds` (default: 1 hour). On lookup, expired
entries are silently removed. The primary store uses Redis when available
(shared across all processes/workers); falls back to an in-process dict.
The existing cosine-similarity ``store()`` / ``lookup()`` API is preserved
for backward compatibility.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from app.tenancy.context import TenantContext


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


@dataclass
class _CacheEntry:
    embedding: list[float]
    response: str
    created_at: float = field(default_factory=time.monotonic)


class SemanticCache:
    """Per-tenant semantic cache with cosine-similarity threshold, TTL, and Redis backend.

    Args:
        threshold: Cosine similarity at or above which a hit is declared (default 0.92).
        ttl_seconds: Seconds before an entry expires (default 3600 = 1 hour).
        redis: Optional async Redis client. When provided, ``get``/``set`` use Redis
            for cross-process sharing. Falls back to in-process dict on any error.
    """

    _CACHE_PREFIX = "semantic_cache:"
    _CACHE_TTL = 3600  # 1 hour (used by Redis-backed get/set)

    def __init__(
        self,
        threshold: float = 0.92,
        ttl_seconds: float = 3600.0,
        redis: Any = None,
    ) -> None:
        self._threshold = threshold
        self._ttl = ttl_seconds
        # Key: tenant_id → list of _CacheEntry  (cosine-similarity API)
        self._entries: dict[str, list[_CacheEntry]] = {}
        self._hits: dict[str, int] = {}    # tenant_id → hit count
        self._misses: dict[str, int] = {}  # tenant_id → miss count
        # Redis client (wired externally after app startup)
        self._redis: Any = redis
        # In-process dict used by the Redis-backed get/set as a fallback
        self._local: dict[str, dict[str, Any]] = {}

    # ── Internal helpers for Redis-backed API ─────────────────────────────────

    def _cache_key(self, tenant_id: str, embedding_hash: str) -> str:
        return f"{self._CACHE_PREFIX}{tenant_id}:{embedding_hash}"

    def _hash_embedding(self, embedding: list[float]) -> str:
        """Compute a short hash of the embedding vector for use as a cache key."""
        import hashlib
        import struct
        packed = struct.pack(f"{len(embedding)}f", *embedding)
        return hashlib.sha256(packed).hexdigest()[:32]

    # ── Redis-backed get / set ─────────────────────────────────────────────────

    async def get(
        self, query: str, embedding: list[float] | None, tenant_id: str
    ) -> str | None:
        """Look up a cached response.

        Uses embedding hash as key when available; falls back to query prefix.
        Checks Redis first (when configured), then local dict.
        """
        if embedding is None:
            return self._local.get(f"{tenant_id}:{query[:100]}", {}).get("response")

        emb_hash = self._hash_embedding(embedding)

        if self._redis is not None:
            try:
                import json
                raw = await self._redis.get(self._cache_key(tenant_id, emb_hash))
                if raw:
                    data = json.loads(raw)
                    return data.get("response")
            except Exception:
                pass  # Fall through to local cache

        return self._local.get(f"{tenant_id}:{emb_hash}", {}).get("response")

    async def set(
        self, query: str, embedding: list[float] | None, response: str, tenant_id: str
    ) -> None:
        """Cache a response keyed by embedding hash (or query prefix when no embedding)."""
        import json

        if embedding is None:
            self._local[f"{tenant_id}:{query[:100]}"] = {"response": response}
            return

        emb_hash = self._hash_embedding(embedding)
        data = json.dumps({"query": query[:200], "response": response[:2000]})

        if self._redis is not None:
            try:
                await self._redis.set(
                    self._cache_key(tenant_id, emb_hash),
                    data,
                    ex=self._CACHE_TTL,
                )
                return  # Redis write succeeded; skip local dict
            except Exception:
                pass  # Fall through to local dict

        self._local[f"{tenant_id}:{emb_hash}"] = {"response": response}

    # ── Cosine-similarity API (backward compatible) ────────────────────────────

    def _prune_expired(self, tenant_id: str) -> None:
        """Remove entries older than TTL."""
        now = time.monotonic()
        entries = self._entries.get(tenant_id, [])
        valid = [e for e in entries if (now - e.created_at) < self._ttl]
        if len(valid) < len(entries):
            self._entries[tenant_id] = valid

    def store(
        self,
        *,
        query_embedding: list[float],
        response: str,
        tenant_ctx: TenantContext,
    ) -> None:
        tid = tenant_ctx.tenant_id
        self._prune_expired(tid)
        self._entries.setdefault(tid, []).append(
            _CacheEntry(embedding=query_embedding, response=response)
        )

    def lookup(
        self,
        *,
        query_embedding: list[float],
        tenant_ctx: TenantContext,
    ) -> str | None:
        tid = tenant_ctx.tenant_id
        self._prune_expired(tid)

        for entry in self._entries.get(tid, []):
            if _cosine(query_embedding, entry.embedding) >= self._threshold:
                self._hits[tid] = self._hits.get(tid, 0) + 1
                return entry.response

        self._misses[tid] = self._misses.get(tid, 0) + 1
        return None

    def stats(self, *, tenant_ctx: TenantContext) -> dict[str, int]:
        """Return hit/miss counts for this tenant."""
        tid = tenant_ctx.tenant_id
        self._prune_expired(tid)
        return {
            "hits": self._hits.get(tid, 0),
            "misses": self._misses.get(tid, 0),
            "cached_entries": len(self._entries.get(tid, [])),
        }

    def clear(self, *, tenant_ctx: TenantContext) -> None:
        """Clear all cached entries for a tenant."""
        tid = tenant_ctx.tenant_id
        self._entries.pop(tid, None)
        self._hits.pop(tid, None)
        self._misses.pop(tid, None)
