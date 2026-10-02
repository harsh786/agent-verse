"""Per-tenant bulkhead semaphores for concurrent tool call limits."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any


class Bulkhead:
    """Async context manager that tracks concurrency without reading private semaphore state."""

    def __init__(self, max_concurrent: int) -> None:
        self._max = max_concurrent
        self._sem = asyncio.Semaphore(max_concurrent)
        self._active = 0  # track ourselves instead of reading private _value attr

    async def __aenter__(self) -> Bulkhead:
        await self._sem.acquire()
        self._active += 1
        return self

    async def __aexit__(self, *args: object) -> None:
        self._active -= 1
        self._sem.release()

    def available_slots(self) -> int:
        return self._max - self._active  # no private attr access


class BulkheadRegistry:
    """Per-tenant asyncio.Semaphore to prevent one tenant monopolizing workers.

    Each tenant gets an isolated semaphore. A runaway tenant cannot consume
    all concurrent tool call slots.
    """

    def __init__(self, default_max_concurrent: int = 20) -> None:
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._default_max = default_max_concurrent
        self._limits: dict[str, int] = {}
        # Tracked active counts per tenant (avoids accessing sem._value)
        self._active_counts: dict[str, int] = {}

    def configure_tenant(self, tenant_id: str, max_concurrent: int) -> None:
        """Set per-tenant concurrency limit."""
        self._limits[tenant_id] = max_concurrent
        # Reset semaphore if limit changed
        self._semaphores.pop(tenant_id, None)
        self._active_counts.pop(tenant_id, None)

    def get(self, tenant_id: str) -> asyncio.Semaphore:
        """Get or create semaphore for tenant."""
        if tenant_id not in self._semaphores:
            limit = self._limits.get(tenant_id, self._default_max)
            self._semaphores[tenant_id] = asyncio.Semaphore(limit)
        return self._semaphores[tenant_id]

    def available_slots(self, tenant_id: str) -> int:
        """How many concurrent calls this tenant can still make."""
        if tenant_id not in self._semaphores:
            return self._limits.get(tenant_id, self._default_max)
        limit = self._limits.get(tenant_id, self._default_max)
        active = self._active_counts.get(tenant_id, 0)
        return max(0, limit - active)


class RedisBulkhead:
    """Redis-backed distributed bulkhead — enforces concurrency limits across ALL workers.

    Uses atomic Lua INCR/DECR with TTL to track concurrent slots.
    Key pattern: bulkhead:{tenant_id}  →  current active count (int, TTL=300s)
    """

    _LUA_ACQUIRE = """
    local key = KEYS[1]
    local limit = tonumber(ARGV[1])
    local ttl = tonumber(ARGV[2])
    local current = tonumber(redis.call('GET', key) or 0)
    if current >= limit then
        return -1
    end
    local new_val = redis.call('INCR', key)
    redis.call('EXPIRE', key, ttl)
    return new_val
    """

    _LUA_RELEASE = """
    local key = KEYS[1]
    local current = tonumber(redis.call('GET', key) or 0)
    if current <= 0 then
        redis.call('SET', key, 0)
        return 0
    end
    return redis.call('DECR', key)
    """

    _SLOT_TTL = 300  # 5 minutes — safety TTL if release not called (e.g., crash)

    def __init__(
        self,
        tenant_id: str,
        max_concurrent: int,
        redis: Any,
        *,
        fallback: asyncio.Semaphore | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._max = max_concurrent
        self._redis = redis
        self._key = f"bulkhead:{tenant_id}"
        # Process-local limit used while Redis is unreachable.
        self._fallback = fallback
        self._holding_fallback = False

    async def acquire(self) -> bool:
        """Try to acquire a slot. Returns True if acquired, False if at limit.

        When Redis is unreachable the call used to return True (fail-open: no
        limit at all). It now degrades to the process-local semaphore when one
        was supplied (still bounded per replica), and otherwise denies.
        """
        try:
            result = await self._redis.eval(
                self._LUA_ACQUIRE, 1, self._key, str(self._max), str(self._SLOT_TTL)
            )
            return int(result) >= 0
        except Exception:
            if self._fallback is None:
                return False
            if self._fallback.locked():
                return False
            await self._fallback.acquire()
            self._holding_fallback = True
            return True

    async def release(self) -> None:
        """Release a previously acquired slot."""
        if self._holding_fallback and self._fallback is not None:
            self._holding_fallback = False
            self._fallback.release()
            return
        with contextlib.suppress(Exception):
            await self._redis.eval(self._LUA_RELEASE, 1, self._key)

    def available_slots_sync(self) -> int:
        """Approximate available slots (non-blocking estimate)."""
        return self._max  # async-only for accurate count

    async def available_slots(self) -> int:
        """Get current available slots from Redis."""
        try:
            current = int(await self._redis.get(self._key) or 0)
            return max(0, self._max - current)
        except Exception:
            return self._max

    async def __aenter__(self) -> RedisBulkhead:
        acquired = await self.acquire()
        if not acquired:
            raise RuntimeError(
                f"Bulkhead full for tenant {self._tenant_id}: at {self._max} concurrent operations"
            )
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.release()


class RedisBulkheadRegistry:
    """Redis-backed registry of per-tenant distributed bulkheads.

    Falls back to asyncio.Semaphore (in-process) when Redis is unavailable.
    """

    def __init__(
        self,
        redis: Any = None,
        default_max_concurrent: int = 20,
    ) -> None:
        self._redis = redis
        self._default_max = default_max_concurrent
        self._limits: dict[str, int] = {}
        # In-process fallback registry
        self._local = BulkheadRegistry(default_max_concurrent=default_max_concurrent)

    def configure_tenant(self, tenant_id: str, max_concurrent: int) -> None:
        """Set per-tenant concurrency limit."""
        self._limits[tenant_id] = max_concurrent
        self._local.configure_tenant(tenant_id, max_concurrent)

    def get_bulkhead(self, tenant_id: str) -> RedisBulkhead | asyncio.Semaphore:
        """Get a bulkhead for a tenant (Redis if available, local otherwise)."""
        limit = self._limits.get(tenant_id, self._default_max)
        if self._redis is not None:
            return RedisBulkhead(
                tenant_id, limit, self._redis, fallback=self._local.get(tenant_id)
            )
        return self._local.get(tenant_id)

    # Expose local registry's get() for backward compat
    def get(self, tenant_id: str) -> asyncio.Semaphore:
        return self._local.get(tenant_id)

    def available_slots(self, tenant_id: str) -> int:
        return self._local.available_slots(tenant_id)


class RedisLeaseLimiter:
    """At most *limit* live leases per key, shared by every process via Redis.

    A sorted set per key: member = lease id, score = expiry (Redis server time,
    ms). Acquire atomically drops expired leases, then adds the member only if
    fewer than *limit* remain, so a crashed holder's slot frees itself when its
    lease expires (an INCR/DECR counter leaked it until the key's TTL, which
    every new acquire refreshed). Holders that run longer than the lease call
    :meth:`refresh`.
    """

    _LUA_ACQUIRE = """
    local key = KEYS[1]
    local limit = tonumber(ARGV[1])
    local lease_ms = tonumber(ARGV[2])
    local member = ARGV[3]
    local t = redis.call('TIME')
    local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
    redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
    if redis.call('ZSCORE', key, member) then
        redis.call('ZADD', key, now + lease_ms, member)
        redis.call('PEXPIRE', key, lease_ms * 2)
        return 1
    end
    if redis.call('ZCARD', key) >= limit then
        return 0
    end
    redis.call('ZADD', key, now + lease_ms, member)
    redis.call('PEXPIRE', key, lease_ms * 2)
    return 1
    """

    _LUA_REFRESH = """
    local key = KEYS[1]
    local lease_ms = tonumber(ARGV[1])
    local member = ARGV[2]
    local t = redis.call('TIME')
    local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
    local score = redis.call('ZSCORE', key, member)
    if (not score) or tonumber(score) <= now then
        return 0
    end
    redis.call('ZADD', key, now + lease_ms, member)
    redis.call('PEXPIRE', key, lease_ms * 2)
    return 1
    """

    _LUA_MEMBERS = """
    local key = KEYS[1]
    local t = redis.call('TIME')
    local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
    redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
    return redis.call('ZRANGE', key, 0, -1)
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def try_acquire(self, key: str, member: str, *, limit: int, lease_s: float) -> bool:
        """Take (or renew) *member*'s lease if the key has room. Raises on Redis errors."""
        res = await self._redis.eval(
            self._LUA_ACQUIRE, 1, key, str(int(limit)), str(int(lease_s * 1000)), member
        )
        return int(res) == 1

    async def refresh(self, key: str, member: str, *, lease_s: float) -> bool:
        """Extend a live lease; ``False`` if it already expired (or was never held)."""
        res = await self._redis.eval(self._LUA_REFRESH, 1, key, str(int(lease_s * 1000)), member)
        return int(res) == 1

    async def release(self, key: str, member: str) -> None:
        await self._redis.zrem(key, member)

    async def members(self, key: str) -> list[str]:
        """The live lease ids for *key*."""
        raw = await self._redis.eval(self._LUA_MEMBERS, 1, key)
        return [m.decode() if isinstance(m, bytes) else str(m) for m in raw or []]


class LocalSlotCounter:
    """Thread-safe, event-loop-agnostic try-acquire counter (per process).

    An ``asyncio.Semaphore`` binds to the loop it first waits on; Celery workers
    run each task in a fresh loop, so a process-wide cap uses a plain lock.
    """

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}

    def try_acquire(self, key: str, limit: int) -> bool:
        with self._lock:
            n = self._counts.get(key, 0)
            if n >= limit:
                return False
            self._counts[key] = n + 1
            return True

    def release(self, key: str) -> None:
        with self._lock:
            n = self._counts.get(key, 0) - 1
            if n <= 0:
                self._counts.pop(key, None)
            else:
                self._counts[key] = n

    def active(self, key: str) -> int:
        with self._lock:
            return self._counts.get(key, 0)
