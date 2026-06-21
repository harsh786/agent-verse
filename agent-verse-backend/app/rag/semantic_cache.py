"""Semantic cache — skip LLM calls when a near-identical query was seen recently.

Uses cosine similarity threshold (default 0.92) to detect near-duplicate queries.
Cache entries are namespaced per tenant to prevent cross-tenant leakage.

In production this would be backed by Redis with a TTL; this in-memory version
is used in tests and serves as the canonical logic.
"""

from __future__ import annotations

import math

from app.tenancy.context import TenantContext


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


class SemanticCache:
    """Per-tenant semantic cache backed by in-memory list.

    Args:
        threshold: Cosine similarity at or above which a hit is declared.
    """

    def __init__(self, threshold: float = 0.92) -> None:
        self._threshold = threshold
        # Key: tenant_id → list of (embedding, response)
        self._entries: dict[str, list[tuple[list[float], str]]] = {}

    def store(
        self,
        *,
        query_embedding: list[float],
        response: str,
        tenant_ctx: TenantContext,
    ) -> None:
        tid = tenant_ctx.tenant_id
        self._entries.setdefault(tid, []).append((query_embedding, response))

    def lookup(
        self,
        *,
        query_embedding: list[float],
        tenant_ctx: TenantContext,
    ) -> str | None:
        tid = tenant_ctx.tenant_id
        for cached_vec, cached_resp in self._entries.get(tid, []):
            if _cosine(query_embedding, cached_vec) >= self._threshold:
                return cached_resp
        return None
