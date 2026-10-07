"""Deduplication cache with TTL.

Hashes expire after `ttl_seconds` (default: 1 hour) so the same operation
can be retried after the window passes.

Cross-replica *goal-submission* dedup lives in ``app.services.dedup`` (a
SHA-256 content key in Redis). The old ``RedisDeduplicationCache`` here keyed on
Python's per-process ``hash(goal)`` (randomised per interpreter, so two replicas
never agreed on a key) and had no callers; it was removed (a08-F192-02).
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
        # tenant_id → {hash: real recorded output}. A dedup hit must serve the step's
        # actual output — previously there was no result store at all and the
        # executor returned a "Duplicate step" placeholder as if it were the result.
        self._results: dict[str, dict[str, str]] = {}

    def _prune_expired(self, tenant_id: str) -> None:
        """Remove hashes older than TTL."""
        now = time.monotonic()
        seen = self._seen.get(tenant_id, {})
        self._seen[tenant_id] = {h: ts for h, ts in seen.items() if now - ts < self._ttl}
        results = self._results.get(tenant_id)
        if results:
            self._results[tenant_id] = {
                h: out for h, out in results.items() if h in self._seen[tenant_id]
            }

    def store_result(self, *, content_hash: str, output: str, tenant_ctx: TenantContext) -> None:
        """Record the real output of an executed step (also marks it seen)."""
        self.mark_seen(content_hash=content_hash, tenant_ctx=tenant_ctx)
        self._results.setdefault(tenant_ctx.tenant_id, {})[content_hash] = output

    def get_result(self, *, content_hash: str, tenant_ctx: TenantContext) -> str | None:
        """The recorded output for *content_hash*, or None when nothing real is stored."""
        self._prune_expired(tenant_ctx.tenant_id)
        return self._results.get(tenant_ctx.tenant_id, {}).get(content_hash)

    def is_duplicate(self, *, content_hash: str, tenant_ctx: TenantContext) -> bool:
        self._prune_expired(tenant_ctx.tenant_id)
        return content_hash in self._seen.get(tenant_ctx.tenant_id, {})

    def mark_seen(self, *, content_hash: str, tenant_ctx: TenantContext) -> None:
        self._prune_expired(tenant_ctx.tenant_id)
        self._seen.setdefault(tenant_ctx.tenant_id, {})[content_hash] = time.monotonic()

    def clear(self, *, tenant_ctx: TenantContext) -> None:
        """Clear all seen hashes for a tenant."""
        self._seen.pop(tenant_ctx.tenant_id, None)
        self._results.pop(tenant_ctx.tenant_id, None)
