"""Sliding-window rate limiters backed by Redis sorted sets.

Two classes are provided:

* ``SlidingWindowRateLimiter`` — wraps ``TenantScopedStore`` (used by the
  FastAPI middleware).  Uses an atomic Lua script (TOCTOU-safe) with a
  per-endpoint ``asyncio.Lock`` fallback when ``eval`` is unavailable.

* ``RateLimiter`` — accepts a raw ``redis.asyncio`` client *or* ``None``.
  When ``None``, uses an in-process asyncio.Lock-guarded sliding window
  (suitable for tests and single-replica deployments without Redis).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from app.tenancy.store import TenantScopedStore

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
        except Exception:
            pass  # fall through to non-atomic fallback

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


class RateLimiter:
    """Atomic sliding-window rate limiter with a graceful in-memory fallback.

    * When *redis* is provided: uses the same ``_LUA_RATE_LIMIT`` Lua script
      via a direct ``redis.asyncio`` client (one round-trip, TOCTOU-safe).
    * When *redis* is ``None``: uses an asyncio.Lock-guarded in-memory
      sliding window — correct within a single process, no external deps.

    Args:
        redis: An ``redis.asyncio``-compatible async client, or ``None`` for
               the in-memory fallback.
        limit: Maximum requests allowed within *window_seconds*.
        window_seconds: Sliding-window length in seconds (default 60).
    """

    def __init__(
        self,
        redis: Any,
        limit: int,
        window_seconds: int = 60,
    ) -> None:
        self._redis = redis
        self._limit = limit
        self._window = window_seconds
        # In-memory fallback: per-tenant list of allowed-request timestamps
        self._mem: dict[str, list[float]] = {}
        # Per-tenant asyncio.Lock for the in-memory path
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, tenant_id: str) -> asyncio.Lock:
        if tenant_id not in self._locks:
            self._locks[tenant_id] = asyncio.Lock()
        return self._locks[tenant_id]

    async def check(self, *, tenant_ctx: Any) -> bool:
        """Return ``True`` if the request is within the rate limit, else ``False``."""
        tenant_id = tenant_ctx.tenant_id

        # ── Redis Lua path ────────────────────────────────────────────────────
        if self._redis is not None:
            try:
                now_ms = int(time.time() * 1000)
                window_ms = self._window * 1000
                key = f"rl:{tenant_id}"
                member = f"{now_ms}:{uuid.uuid4().hex}"
                result = await self._redis.eval(
                    _LUA_RATE_LIMIT,
                    1,
                    key,
                    str(now_ms),
                    str(window_ms),
                    str(self._limit),
                    member,
                )
                return bool(result[0])
            except Exception:
                pass  # fall through to in-memory fallback

        # ── In-memory fallback with asyncio.Lock ─────────────────────────────
        lock = self._get_lock(tenant_id)
        async with lock:
            now = time.time()
            window_start = now - self._window
            timestamps = self._mem.get(tenant_id, [])
            # Prune expired entries
            timestamps = [t for t in timestamps if t > window_start]
            if len(timestamps) >= self._limit:
                self._mem[tenant_id] = timestamps
                return False
            timestamps.append(now)
            self._mem[tenant_id] = timestamps
            return True
