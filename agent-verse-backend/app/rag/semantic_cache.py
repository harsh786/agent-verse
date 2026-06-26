"""Semantic cache with TTL support.

Entries expire after `ttl_seconds` (default: 1 hour). On lookup, expired
entries are silently removed. Backed by in-memory list; production uses Redis.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

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
    """Per-tenant semantic cache with cosine-similarity threshold and TTL.

    Args:
        threshold: Cosine similarity at or above which a hit is declared (default 0.92).
        ttl_seconds: Seconds before an entry expires (default 3600 = 1 hour).
    """

    def __init__(self, threshold: float = 0.92, ttl_seconds: float = 3600.0) -> None:
        self._threshold = threshold
        self._ttl = ttl_seconds
        # Key: tenant_id → list of _CacheEntry
        self._entries: dict[str, list[_CacheEntry]] = {}
        self._hits: dict[str, int] = {}    # tenant_id → hit count
        self._misses: dict[str, int] = {}  # tenant_id → miss count

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
