"""Per-tenant bulkhead semaphores for concurrent tool call limits."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

_log = logging.getLogger(__name__)


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
    """Redis-backed distributed bulkhead — enforces a tenant's limit across ALL workers.

    Each acquisition is a lease (a member of the sorted set
    ``bulkhead_leases:{tenant_id}``, see :class:`RedisLeaseLimiter`) that a
    background task renews while the slot is held, so

    * a crashed holder's slot frees itself when its lease expires — the old
      INCR/DECR counter re-armed its 300 s TTL on every acquire, so under steady
      traffic a leaked slot never expired;
    * a step that runs longer than the lease keeps its slot — the counter's TTL
      could expire mid-step and reset the count to zero for everyone
      (a08-F199-03).

    While Redis is unreachable the call degrades to a process-local limit when a
    fallback was supplied (still bounded per replica), and otherwise denies.
    """

    _LEASE_S = 300.0

    def __init__(
        self,
        tenant_id: str,
        max_concurrent: int,
        redis: Any,
        *,
        fallback: LocalSlotCounter | asyncio.Semaphore | None = None,
        lease_s: float | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._max = max_concurrent
        self._redis = redis
        self._key = f"bulkhead_leases:{tenant_id}"
        self._limiter = RedisLeaseLimiter(redis)
        self._lease_s = float(lease_s if lease_s is not None else self._LEASE_S)
        # Process-local limit used while Redis is unreachable.
        self._fallback = fallback
        self._holding_fallback = False
        self._member: str | None = None
        self._keepalive: asyncio.Task[None] | None = None

    async def acquire(self) -> bool:
        """Try to acquire a slot. Returns True if acquired, False if at limit."""
        import uuid

        member = uuid.uuid4().hex
        try:
            acquired = await self._limiter.try_acquire(
                self._key, member, limit=self._max, lease_s=self._lease_s
            )
        except Exception as exc:
            _log.warning(
                "bulkhead_redis_unavailable tenant_id=%s error=%s", self._tenant_id, exc
            )
            return await self._acquire_fallback()
        if acquired:
            self._member = member
            self._keepalive = asyncio.create_task(self._renew(member))
        return acquired

    async def _acquire_fallback(self) -> bool:
        fb = self._fallback
        if fb is None:
            return False
        if isinstance(fb, LocalSlotCounter):
            if not fb.try_acquire(self._key, self._max):
                return False
        else:
            if fb.locked():
                return False
            await fb.acquire()  # a slot is free: completes without waiting
        self._holding_fallback = True
        return True

    async def _renew(self, member: str) -> None:
        """Keep the lease alive while the slot is held."""
        while True:
            await asyncio.sleep(self._lease_s / 3)
            try:
                alive = await self._limiter.refresh(self._key, member, lease_s=self._lease_s)
            except Exception as exc:
                _log.warning(
                    "bulkhead_lease_renew_failed tenant_id=%s error=%s", self._tenant_id, exc
                )
                continue
            if not alive:
                _log.warning("bulkhead_lease_lost tenant_id=%s", self._tenant_id)
                return

    async def release(self) -> None:
        """Release a previously acquired slot."""
        if self._holding_fallback and self._fallback is not None:
            self._holding_fallback = False
            if isinstance(self._fallback, LocalSlotCounter):
                self._fallback.release(self._key)
            else:
                self._fallback.release()
            return
        member, self._member = self._member, None
        task, self._keepalive = self._keepalive, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        if member is None:
            return
        try:
            await self._limiter.release(self._key, member)
        except Exception as exc:
            # Not silent: the slot stays taken until its lease expires.
            _log.warning(
                "bulkhead_release_failed tenant_id=%s error=%s (slot frees in %.0fs)",
                self._tenant_id,
                exc,
                self._lease_s,
            )

    async def available_slots(self) -> int:
        """Free slots right now, from the live leases in Redis (raises on a Redis error)."""
        return max(0, self._max - len(await self._limiter.members(self._key)))

    async def __aenter__(self) -> RedisBulkhead:
        acquired = await self.acquire()
        if not acquired:
            raise RuntimeError(
                f"Bulkhead full for tenant {self._tenant_id}: at {self._max} concurrent operations"
            )
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.release()


# One process-wide, event-loop-agnostic fallback for every registry in this
# process: an asyncio.Semaphore binds to one loop, and a worker registry built
# per goal had a fallback "limit" per goal (a08-F199-04).
_PROCESS_FALLBACK: LocalSlotCounter | None = None


def _process_fallback() -> LocalSlotCounter:
    global _PROCESS_FALLBACK
    if _PROCESS_FALLBACK is None:
        _PROCESS_FALLBACK = LocalSlotCounter()
    return _PROCESS_FALLBACK


class RedisBulkheadRegistry:
    """Redis-backed registry of per-tenant distributed bulkheads.

    Falls back to asyncio.Semaphore (in-process) when no Redis client is wired.
    """

    def __init__(
        self,
        redis: Any = None,
        default_max_concurrent: int = 20,
    ) -> None:
        self._redis = redis
        self._default_max = default_max_concurrent
        self._limits: dict[str, int] = {}
        # In-process registry used when no Redis client is wired.
        self._local = BulkheadRegistry(default_max_concurrent=default_max_concurrent)

    def configure_tenant(self, tenant_id: str, max_concurrent: int) -> None:
        """Set per-tenant concurrency limit."""
        self._limits[tenant_id] = max_concurrent
        self._local.configure_tenant(tenant_id, max_concurrent)

    def limit_for(self, tenant_id: str) -> int:
        return self._limits.get(tenant_id, self._default_max)

    def get_bulkhead(self, tenant_id: str) -> RedisBulkhead | asyncio.Semaphore:
        """Get a bulkhead for a tenant (Redis if available, local otherwise)."""
        if self._redis is not None:
            return RedisBulkhead(
                tenant_id, self.limit_for(tenant_id), self._redis, fallback=_process_fallback()
            )
        return self._local.get(tenant_id)

    # Expose local registry's get() for backward compat
    def get(self, tenant_id: str) -> asyncio.Semaphore:
        return self._local.get(tenant_id)

    async def available_slots(self, tenant_id: str) -> int:
        """The tenant's free slots: the fleet-wide count in Redis when wired.

        It used to read the process-local registry, which never sees another
        replica's (or even this replica's Redis) slots (a08-F199-01).
        """
        bulkhead = self.get_bulkhead(tenant_id)
        if isinstance(bulkhead, RedisBulkhead):
            return await bulkhead.available_slots()
        return self._local.available_slots(tenant_id)


class LoopLocalRedis:
    """An async Redis client per running event loop, closed when the loop ends.

    Celery tasks run each goal on a fresh loop (``run_in_fresh_loop``) and
    ``redis.asyncio`` clients are loop-bound. Building a client per ``run_goal``
    and never closing it leaked one client (and its connections) per goal
    (a08-F199-04); this proxy opens one per loop on first use and registers its
    ``aclose`` with ``on_loop_teardown``.
    """

    def __init__(self, url: str) -> None:
        import weakref

        self._url = url
        self._clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any] = (
            weakref.WeakKeyDictionary()
        )

    def _client(self) -> Any:
        loop = asyncio.get_running_loop()
        client = self._clients.get(loop)
        if client is None:
            import redis.asyncio as aioredis

            from app.db.session import on_loop_teardown

            client = aioredis.from_url(self._url, decode_responses=True)
            self._clients[loop] = client
            on_loop_teardown(client.aclose)
        return client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client(), name)


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
