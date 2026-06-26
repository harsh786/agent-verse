"""Redis-backed idempotency store for goal submissions."""
from __future__ import annotations

from typing import Any


class IdempotencyStore:
    """Prevents duplicate goal submissions using Redis SET NX with TTL.

    Keyed by caller-supplied idempotency key (or goal content hash).
    Returns False if the key was already seen (duplicate request).
    """

    KEY_PREFIX = "idempotency:"

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def check_and_set(
        self, key: str, tenant_id: str, ttl_seconds: int = 3600
    ) -> bool:
        """Return True if key is new (should process), False if duplicate."""
        redis_key = f"{self.KEY_PREFIX}{tenant_id}:{key}"
        result = await self._redis.set(redis_key, "1", nx=True, ex=ttl_seconds)
        return result is not None  # None → key already exists → duplicate

    async def release(self, key: str, tenant_id: str) -> None:
        """Release an idempotency key (e.g., if the request failed and should be retried)."""
        redis_key = f"{self.KEY_PREFIX}{tenant_id}:{key}"
        await self._redis.delete(redis_key)

    async def exists(self, key: str, tenant_id: str) -> bool:
        """Check if key exists without setting it."""
        redis_key = f"{self.KEY_PREFIX}{tenant_id}:{key}"
        return bool(await self._redis.exists(redis_key))
