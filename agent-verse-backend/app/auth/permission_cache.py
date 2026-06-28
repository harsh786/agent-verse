"""Redis-backed permission cache for API key scope resolution.

Cache key:  perm:{tenant_id}:{key_id}
Value:      JSON list of granted scope strings
TTL:        300 seconds (5 minutes)

Invalidation:
  - ``invalidate(tenant_id, key_id)`` — single key/tenant pair
  - ``invalidate_tenant(tenant_id)`` — all keys for a tenant (called on role mutation)
"""

from __future__ import annotations

import json
from typing import Any


class PermissionCache:
    """Redis-backed permission set cache with 5-minute TTL."""

    TTL = 300  # seconds
    PREFIX = "perm:"

    def __init__(self, redis: Any) -> None:
        self._r = redis

    def _key(self, tenant_id: str, key_id: str) -> str:
        return f"{self.PREFIX}{tenant_id}:{key_id}"

    async def get(self, tenant_id: str, key_id: str) -> set[str] | None:
        """Return cached scope set or None on cache miss."""
        raw = await self._r.get(self._key(tenant_id, key_id))
        if raw is None:
            return None
        return set(json.loads(raw))

    async def set(self, tenant_id: str, key_id: str, scopes: set[str]) -> None:
        """Store scope set with TTL."""
        await self._r.setex(
            self._key(tenant_id, key_id),
            self.TTL,
            json.dumps(sorted(scopes)),
        )

    async def invalidate(self, tenant_id: str, key_id: str) -> None:
        """Remove a single permission cache entry."""
        await self._r.delete(self._key(tenant_id, key_id))

    async def invalidate_tenant(self, tenant_id: str) -> None:
        """Bust all cached permissions for a tenant after a role mutation.

        Uses SCAN to avoid blocking the Redis server with KEYS.
        """
        cursor = 0
        pattern = f"{self.PREFIX}{tenant_id}:*"
        while True:
            cursor, keys = await self._r.scan(cursor, match=pattern, count=200)
            if keys:
                await self._r.delete(*keys)
            if cursor == 0:
                break
