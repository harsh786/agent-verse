"""Per-tenant bulkhead: limits concurrent in-flight trigger goals."""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

PLAN_CONCURRENCY: dict[str, int] = {
    "free": 2,
    "starter": 10,
    "professional": 100,
    "enterprise": 999,
}


class TriggerBulkhead:
    """Redis-backed per-tenant concurrency limiter."""

    def __init__(self, redis: object | None = None) -> None:
        self._redis = redis

    def _key(self, tenant_id: str) -> str:
        return f"trigger_bulkhead:{tenant_id}"

    async def acquire(self, tenant_id: str, plan: str = "free") -> bool:
        """Return True if the slot was acquired (trigger can proceed)."""
        cap = PLAN_CONCURRENCY.get(plan, 2)
        if self._redis is None:
            return True

        key = self._key(tenant_id)
        try:
            current = await self._redis.incr(key)
            # Set expiry on first increment to prevent leaks
            if current == 1:
                await self._redis.expire(key, 300)  # 5 min safety TTL
            if current > cap:
                await self._redis.decr(key)
                return False
            return True
        except Exception:
            _log.warning("bulkhead_redis_error tenant_id=%s", tenant_id)
            return True

    async def release(self, tenant_id: str) -> None:
        """Release a bulkhead slot."""
        if self._redis is None:
            return
        key = self._key(tenant_id)
        try:
            val = await self._redis.decr(key)
            if val < 0:
                await self._redis.set(key, 0)
        except Exception:
            _log.warning("bulkhead_release_error tenant_id=%s", tenant_id)
