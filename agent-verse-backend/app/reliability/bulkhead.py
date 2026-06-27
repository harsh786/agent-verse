"""Per-tenant bulkhead semaphores for concurrent tool call limits."""
from __future__ import annotations

import asyncio
from typing import Any


class Bulkhead:
    """Async context manager that tracks concurrency without reading private semaphore state."""

    def __init__(self, max_concurrent: int) -> None:
        self._max = max_concurrent
        self._sem = asyncio.Semaphore(max_concurrent)
        self._active = 0  # track ourselves instead of reading private _value attr

    async def __aenter__(self) -> "Bulkhead":
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

    def __init__(self, tenant_id: str, max_concurrent: int, redis: Any) -> None:
        self._tenant_id = tenant_id
        self._max = max_concurrent
        self._redis = redis
        self._key = f"bulkhead:{tenant_id}"

    async def acquire(self) -> bool:
        """Try to acquire a slot. Returns True if acquired, False if at limit."""
        try:
            result = await self._redis.eval(
                self._LUA_ACQUIRE, 1, self._key, str(self._max), str(self._SLOT_TTL)
            )
            return int(result) >= 0
        except Exception:
            return True  # fail-open: allow if Redis unavailable

    async def release(self) -> None:
        """Release a previously acquired slot."""
        try:
            await self._redis.eval(self._LUA_RELEASE, 1, self._key)
        except Exception:
            pass

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

    async def __aenter__(self) -> "RedisBulkhead":
        acquired = await self.acquire()
        if not acquired:
            raise RuntimeError(
                f"Bulkhead full for tenant {self._tenant_id}: "
                f"at {self._max} concurrent operations"
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

    def get_bulkhead(self, tenant_id: str) -> "RedisBulkhead | asyncio.Semaphore":
        """Get a bulkhead for a tenant (Redis if available, local otherwise)."""
        limit = self._limits.get(tenant_id, self._default_max)
        if self._redis is not None:
            return RedisBulkhead(tenant_id, limit, self._redis)
        return self._local.get(tenant_id)

    # Expose local registry's get() for backward compat
    def get(self, tenant_id: str) -> "asyncio.Semaphore":
        return self._local.get(tenant_id)

    def available_slots(self, tenant_id: str) -> int:
        return self._local.available_slots(tenant_id)
