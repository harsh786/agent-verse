"""Per-channel rate limiter — Q11 of spec.

Limits:
  Per tenant across all channels: 100 commands/hour
  start_mission:   10/hour (prevent spam)
  approve:         50/hour
  read operations: unlimited

Implementation: Redis sliding window counter.
Falls back to in-memory if Redis unavailable.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from opentelemetry import trace

from app.observability.logging import get_logger

_log = get_logger(__name__)
_tracer = trace.get_tracer(__name__)


class RateLimitExceededError(Exception):
    """Raised when a channel command exceeds rate limits."""

    def __init__(self, limit: int, window_seconds: int, retry_after: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.retry_after = retry_after
        super().__init__(f"Rate limit {limit}/window exceeded. Retry after {retry_after}s")


# ── Per-channel limits (commands per window) ──────────────────────────────────

CHANNEL_LIMITS: dict[str, dict[str, Any]] = {
    "rest": {"limit": 100, "window": 3600},  # 100/hour
    "telegram": {"limit": 60, "window": 3600},  # 60/hour
    "slack": {"limit": 60, "window": 3600},
    "discord": {"limit": 60, "window": 3600},
    "whatsapp": {"limit": 30, "window": 3600},
    "email": {"limit": 10, "window": 3600},
    "mcp": {"limit": 200, "window": 3600},
    "a2a": {"limit": 500, "window": 3600},
    "webhook": {"limit": 300, "window": 3600},
    "voice_webhook": {"limit": 20, "window": 3600},
    # Voice OS endpoint rate limits (spec Task 5.3)
    "voice_transcribe": {"limit": 30, "window": 60},
    "voice_speak": {"limit": 20, "window": 60},
    "voice_greeting": {"limit": 10, "window": 60},
    "voice_persona": {"limit": 5, "window": 60},
    "voice_stream": {"limit": 5, "window": 300},  # 5 concurrent WS sessions
}

# Action-level limits (applied on top of channel limits)
ACTION_LIMITS: dict[str, dict[str, Any]] = {
    "start_mission": {"limit": 10, "window": 3600},
    "approve": {"limit": 50, "window": 3600},
    "pause_org": {"limit": 5, "window": 3600},
    "delete_mission": {"limit": 3, "window": 3600},
}


class ChannelRateLimiter:
    """
    Sliding window rate limiter for gateway channels.
    Uses Redis when available; falls back to in-memory dict.
    """

    def __init__(self, redis_client: Any | None = None) -> None:
        self._redis = redis_client
        # in-memory fallback: {key: [(timestamp, count), ...]}
        self._memory: dict[str, list[float]] = defaultdict(list)

    async def check_and_increment(
        self,
        tenant_id: str,
        org_id: str,
        channel: str,
        actor_id: str,
        action: str | None = None,
    ) -> dict[str, Any]:
        """
        Check rate limits and increment counter.
        Returns: {"allowed": bool, "remaining": int, "reset_at": int}
        Raises: RateLimitExceededError if limit exceeded.
        """
        with _tracer.start_as_current_span("rate_limiter.check") as span:
            span.set_attribute("channel", channel)
            span.set_attribute("action", action or "command")

            channel_cfg = CHANNEL_LIMITS.get(channel, CHANNEL_LIMITS["rest"])
            limit = channel_cfg["limit"]
            window = channel_cfg["window"]

            key = f"rl:{tenant_id}:{org_id}:{channel}:{actor_id}"

            if self._redis:
                result = await self._check_redis(key, limit, window)
            else:
                result = self._check_memory(key, limit, window)

            if not result["allowed"]:
                raise RateLimitExceededError(limit, window, result["retry_after"])

            # Apply action-level limit if specified
            if action and action in ACTION_LIMITS:
                action_cfg = ACTION_LIMITS[action]
                akey = f"rl:action:{tenant_id}:{org_id}:{action}:{actor_id}"
                if self._redis:
                    aresult = await self._check_redis(
                        akey, action_cfg["limit"], action_cfg["window"]
                    )
                else:
                    aresult = self._check_memory(akey, action_cfg["limit"], action_cfg["window"])
                if not aresult["allowed"]:
                    raise RateLimitExceededError(
                        action_cfg["limit"], action_cfg["window"], aresult["retry_after"]
                    )

            return result

    async def _check_redis(self, key: str, limit: int, window: int) -> dict[str, Any]:
        """Redis sliding window (ZADD + ZCOUNT pattern)."""
        now = int(time.time() * 1000)
        expire_before = now - (window * 1000)

        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(key, 0, expire_before)
        pipe.zcard(key)
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, window + 60)
        results = await pipe.execute()

        current_count = results[1]
        allowed = current_count < limit

        return {
            "allowed": allowed,
            "remaining": max(0, limit - current_count - 1),
            "limit": limit,
            "retry_after": window if not allowed else 0,
            "reset_at": now + (window * 1000),
        }

    def _check_memory(self, key: str, limit: int, window: int) -> dict[str, Any]:
        """In-memory sliding window fallback."""
        now = time.time()
        window_start = now - window
        timestamps = self._memory[key]

        # Remove expired entries
        self._memory[key] = [t for t in timestamps if t > window_start]
        count = len(self._memory[key])

        if count >= limit:
            oldest = min(self._memory[key]) if self._memory[key] else now
            retry_after = int(oldest + window - now) + 1
            return {
                "allowed": False,
                "remaining": 0,
                "limit": limit,
                "retry_after": retry_after,
                "reset_at": int(oldest + window),
            }

        self._memory[key].append(now)
        return {
            "allowed": True,
            "remaining": limit - count - 1,
            "limit": limit,
            "retry_after": 0,
            "reset_at": int(now + window),
        }
