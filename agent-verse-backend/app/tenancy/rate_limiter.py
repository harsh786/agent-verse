"""Sliding-window rate limiter backed by Redis sorted sets.

``SlidingWindowRateLimiter`` wraps ``TenantScopedStore`` (used by the FastAPI
middleware): an atomic Lua script (TOCTOU-safe) with a per-endpoint
``asyncio.Lock`` fallback when ``eval`` is unavailable. It is the single
implementation — the unused ``RateLimiter`` class with a per-process
in-memory window was removed (RATE-02): a per-process counter enforces N x the
limit across N replicas.
"""

from __future__ import annotations

import asyncio
import time
import uuid

from app.observability.logging import get_logger
from app.tenancy.store import TenantScopedStore

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Atomic Lua script — TOCTOU-safe rate-limit check+record in a single round-trip
# ---------------------------------------------------------------------------
# KEYS[1]  — the (already tenant-prefixed) sorted-set key
# ARGV[1]  — current timestamp in milliseconds (integer string)
# ARGV[2]  — window length in milliseconds (integer string)
# ARGV[3]  — requests-per-window limit (integer string)
# ARGV[4]  — unique member for this request (prevents duplicate members)
#
# Returns  — {0, 0} when limit exceeded; {1, remaining} when allowed.
_LUA_RATE_LIMIT = """
local key        = KEYS[1]
local now        = tonumber(ARGV[1])
local window_ms  = tonumber(ARGV[2])
local limit      = tonumber(ARGV[3])
local member     = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window_ms)
local count = redis.call('ZCARD', key)
if count >= limit then
    return {0, 0}
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, math.ceil(window_ms / 1000) + 1)
return {1, limit - count - 1}
"""


class SlidingWindowRateLimiter:
    """Per-tenant, per-endpoint sliding-window rate limiter.

    Primary path: atomic Lua script via ``TenantScopedStore.eval`` (one
    Redis round-trip, no TOCTOU).

    Fallback path (eval unavailable / Redis error): non-atomic 3-op sequence
    protected by a per-endpoint ``asyncio.Lock`` for in-process safety.

    Args:
        store: Tenant-scoped Redis store.
        window_seconds: Length of the sliding window (default 60 s = per-minute).
    """

    def __init__(self, store: TenantScopedStore, *, window_seconds: int = 60) -> None:
        self._store = store
        self._window = window_seconds
        # Per-endpoint in-process locks used only in the non-atomic fallback path
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, endpoint: str) -> asyncio.Lock:
        if endpoint not in self._locks:
            self._locks[endpoint] = asyncio.Lock()
        return self._locks[endpoint]

    async def check_and_record(
        self,
        endpoint: str,
        *,
        limit: int,
        now: float | None = None,
    ) -> tuple[bool, int, float]:
        """Check if a request is within rate limits and record it if allowed.

        Returns:
            (allowed, remaining, reset_at_epoch_seconds)
        """
        ts = now if now is not None else time.time()
        now_ms = int(ts * 1000)
        window_ms = self._window * 1000
        key = f"rl:{endpoint}"
        member = f"{now_ms}:{uuid.uuid4().hex}"
        reset_at = ts + self._window

        # ── Primary: atomic Lua eval (TOCTOU-safe) ────────────────────────────
        try:
            result = await self._store.eval(
                _LUA_RATE_LIMIT,
                1,  # numkeys
                key,  # KEYS[1]  (store.eval will tenant-prefix this)
                str(now_ms),
                str(window_ms),
                str(limit),
                member,
            )
            return bool(result[0]), int(result[1]), reset_at
        except Exception as exc:
            # Redis (shared, cluster-wide) rate limiting degraded to the per-pod
            # fallback — with N pods this enforces up to Nx the intended global
            # limit. Log it so the degradation is visible instead of silent.
            _log.warning("rate_limiter_redis_degraded_to_per_pod", error=str(exc)[:200])

        # ── Fallback: 3-op sequence with asyncio.Lock for in-process safety ──
        lock = self._get_lock(endpoint)
        async with lock:
            window_start = ts - self._window
            await self._store.zremrangebyscore(key, 0.0, window_start)
            count = await self._store.zcard(key)
            if count >= limit:
                return False, 0, reset_at
            await self._store.zadd(key, {member: ts})
            await self._store.expire(key, self._window * 2)
            return True, limit - count - 1, reset_at
