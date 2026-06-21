"""Deduplication cache — prevent identical tool calls from executing twice.

Uses content hash (SHA-256 of tool name + args) to detect duplicates. Hashes
are stored per-tenant to prevent cross-tenant collisions.

In production this uses a Redis SET with a per-key TTL (defaults to 1 hour).
This in-memory version is used in tests.
"""

from __future__ import annotations

from app.tenancy.context import TenantContext


class DeduplicationCache:
    """In-memory deduplication cache, namespaced per tenant."""

    def __init__(self) -> None:
        self._seen: dict[str, set[str]] = {}

    def is_duplicate(self, *, content_hash: str, tenant_ctx: TenantContext) -> bool:
        return content_hash in self._seen.get(tenant_ctx.tenant_id, set())

    def mark_seen(self, *, content_hash: str, tenant_ctx: TenantContext) -> None:
        self._seen.setdefault(tenant_ctx.tenant_id, set()).add(content_hash)
