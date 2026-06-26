"""Redis-backed distributed lock for at-most-once goal execution."""
from __future__ import annotations

import uuid
from typing import Any


class GoalExecutionLock:
    """Redis SET NX PX lock ensuring at-most-once execution per cluster.

    Uses a Lua script for atomic release so we never delete another
    worker's lock even if our TTL expires.
    """

    KEY_PREFIX = "goal_lock:"
    _RELEASE_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """
    _EXTEND_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("pexpire", KEYS[1], ARGV[2])
    else
        return 0
    end
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis
        self._lock_value = uuid.uuid4().hex  # unique per lock instance

    async def acquire(self, goal_id: str, ttl_ms: int = 1_800_000) -> bool:
        """Returns True if lock acquired, False if another worker holds it."""
        key = f"{self.KEY_PREFIX}{goal_id}"
        result = await self._redis.set(key, self._lock_value, nx=True, px=ttl_ms)
        return result is not None

    async def release(self, goal_id: str) -> None:
        """Release lock only if we own it (Lua atomic check-and-delete)."""
        key = f"{self.KEY_PREFIX}{goal_id}"
        try:
            await self._redis.eval(self._RELEASE_SCRIPT, 1, key, self._lock_value)
        except Exception:
            pass

    async def extend(self, goal_id: str, ttl_ms: int = 1_800_000) -> bool:
        """Extend TTL if we still own the lock."""
        key = f"{self.KEY_PREFIX}{goal_id}"
        try:
            result = await self._redis.eval(
                self._EXTEND_SCRIPT, 1, key, self._lock_value, str(ttl_ms)
            )
            return bool(result)
        except Exception:
            return False

    async def is_locked(self, goal_id: str) -> bool:
        """Check if any worker holds a lock for this goal."""
        key = f"{self.KEY_PREFIX}{goal_id}"
        try:
            return await self._redis.exists(key) > 0
        except Exception:
            return False
