"""
Goal-level request deduplication.

When two tenants submit identical goals within a short window (default 60s),
the second submission does NOT launch a new agent execution — it attaches to
the first goal and polls for the same result.

Key design:
- Dedup key = sha256(tenant_id + "\x00" + goal.strip().lower())[:32]
- Storage: Redis SET with 60s TTL, value = first goal_id
- Race-free: SET NX (set if not exists) is atomic
- If the original goal fails, the dedup key is deleted so the next submit retries

This prevents duplicate Celery tasks for the same tenant submitting the same
goal twice (e.g. from double-click, retry button, or CI pipelines).
"""

from __future__ import annotations

import contextlib
import hashlib
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

_TTL = 60  # seconds


def _dedup_key(tenant_id: str, goal: str) -> str:
    payload = tenant_id + "\x00" + goal.strip().lower()
    return "goal_dedup:" + hashlib.sha256(payload.encode()).hexdigest()[:32]


class GoalDeduplicator:
    """
    Redis-backed goal-level deduplication.

    Usage:
        dedup = GoalDeduplicator(redis=redis_client)

        # Before creating a new goal:
        existing_id = await dedup.get_existing(tenant_id, goal)
        if existing_id:
            return {"goal_id": existing_id, "deduplicated": True}

        # After creating the goal:
        await dedup.register(tenant_id, goal, new_goal_id)

        # When the goal completes or fails:
        await dedup.release(tenant_id, goal)
    """

    def __init__(self, redis: Any = None, ttl: int = _TTL) -> None:
        self._redis = redis
        self._ttl = ttl
        # In-memory fallback: key → goal_id
        self._mem: dict[str, str] = {}

    async def get_existing(self, tenant_id: str, goal: str) -> str | None:
        """Return the in-flight goal_id for this (tenant, goal) pair, or None."""
        key = _dedup_key(tenant_id, goal)
        if self._redis is not None:
            try:
                val = await self._redis.get(key)
                if val:
                    goal_id = val.decode() if isinstance(val, bytes) else val
                    logger.info("goal_deduplicated", tenant=tenant_id, goal_id=goal_id)
                    return goal_id
            except Exception as exc:
                logger.debug("dedup_get_error", error=str(exc)[:60])
        return self._mem.get(key)

    async def register(self, tenant_id: str, goal: str, goal_id: str) -> bool:
        """
        Register a new goal. Returns True if this is the first registration
        (i.e. no duplicate exists). Returns False if a duplicate was already
        registered concurrently (caller should use the existing goal_id).
        """
        key = _dedup_key(tenant_id, goal)
        if self._redis is not None:
            try:
                # SET NX: only sets if key doesn't exist (atomic)
                set_result = await self._redis.set(key, goal_id, ex=self._ttl, nx=True)
                if set_result:
                    return True
                # Key already existed — someone else registered first
                return False
            except Exception as exc:
                logger.debug("dedup_register_error", error=str(exc)[:60])
        self._mem[key] = goal_id
        return True

    async def release(self, tenant_id: str, goal: str) -> None:
        """Delete the dedup key so future identical goals can be submitted."""
        key = _dedup_key(tenant_id, goal)
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.delete(key)
        self._mem.pop(key, None)


# Module-level in-memory singleton (upgraded with Redis in lifespan)
_default_deduplicator = GoalDeduplicator()
