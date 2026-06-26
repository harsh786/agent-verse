"""Deduplication cache with TTL.

Hashes expire after `ttl_seconds` (default: 1 hour) so the same operation
can be retried after the window passes.
"""
from __future__ import annotations

import time

from app.tenancy.context import TenantContext


class DeduplicationCache:
    """In-memory deduplication cache with TTL, namespaced per tenant."""

    def __init__(self, ttl_seconds: float = 3600.0) -> None:
        self._ttl = ttl_seconds
        # tenant_id → {hash: timestamp}
        self._seen: dict[str, dict[str, float]] = {}

    def _prune_expired(self, tenant_id: str) -> None:
        """Remove hashes older than TTL."""
        now = time.monotonic()
        seen = self._seen.get(tenant_id, {})
        self._seen[tenant_id] = {h: ts for h, ts in seen.items() if now - ts < self._ttl}

    def is_duplicate(self, *, content_hash: str, tenant_ctx: TenantContext) -> bool:
        self._prune_expired(tenant_ctx.tenant_id)
        return content_hash in self._seen.get(tenant_ctx.tenant_id, {})

    def mark_seen(self, *, content_hash: str, tenant_ctx: TenantContext) -> None:
        self._prune_expired(tenant_ctx.tenant_id)
        self._seen.setdefault(tenant_ctx.tenant_id, {})[content_hash] = time.monotonic()

    def clear(self, *, tenant_ctx: TenantContext) -> None:
        """Clear all seen hashes for a tenant."""
        self._seen.pop(tenant_ctx.tenant_id, None)
