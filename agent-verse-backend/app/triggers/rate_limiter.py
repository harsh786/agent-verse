"""Trigger rate limiter using Redis INCR with TTL sliding window."""
from __future__ import annotations

import logging
import time

_log = logging.getLogger(__name__)

# Plan-tier maximum firings per hour
PLAN_CAPS: dict[str, int] = {
    "free":         10,
    "starter":      60,
    "professional": 600,
    "enterprise":   999_999,
}


def effective_rate_cap(spec_max: int, plan: str) -> int:
    """Return the effective per-hour cap: min(user setting, plan ceiling)."""
    plan_cap = PLAN_CAPS.get(plan, 10)
    if spec_max == 0:
        return plan_cap
    return min(spec_max, plan_cap)


class TriggerRateLimiter:
    """Redis-backed sliding window rate limiter for trigger firings."""

    def __init__(self, redis: object | None = None) -> None:
        self._redis = redis

    async def check(
        self,
        trigger_id: str,
        max_per_hour: int,
        plan: str = "free",
    ) -> bool:
        """Return True if the trigger is allowed to fire, False if rate-limited."""
        cap = effective_rate_cap(max_per_hour, plan)
        if cap <= 0 or cap >= 999_999:
            return True

        if self._redis is None:
            return True  # no Redis — allow (degraded mode)

        key = f"trigger_rate:{trigger_id}:{int(time.time()) // 3600}"
        try:
            count = await self._redis.incr(key)
            if count == 1:
                await self._redis.expire(key, 3600)
            return count <= cap
        except Exception:
            _log.warning("rate_limiter_redis_error trigger_id=%s", trigger_id)
            return True  # fail open
