"""TenantScopedStore — Redis wrapper that namespaces every key under the tenant.

All keys are prefixed `tenant:{tenant_id}:` so two tenants sharing one Redis
instance can never accidentally read each other's data.
"""

from __future__ import annotations

from typing import Any


class TenantScopedStore:
    """Tenant-isolated Redis interface.

    Wraps any redis.asyncio.Redis-compatible client. The *only* difference from
    the raw client is that every key is transparently prefixed — callers never
    need to remember the prefix themselves.
    """

    def __init__(self, redis: Any, tenant_id: str) -> None:
        self._redis = redis
        self._prefix = f"tenant:{tenant_id}:"

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    # ── string ops ─────────────────────────────────────────────────────────

    async def get(self, key: str) -> str | None:
        result: bytes | str | None = await self._redis.get(self._key(key))
        if result is None:
            return None
        return result.decode() if isinstance(result, bytes) else result

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        await self._redis.set(self._key(key), value, ex=ex)

    async def delete(self, *keys: str) -> int:
        result: int = await self._redis.delete(*[self._key(k) for k in keys])
        return result

    async def exists(self, *keys: str) -> int:
        result: int = await self._redis.exists(*[self._key(k) for k in keys])
        return result

    async def incr(self, key: str) -> int:
        result: int = await self._redis.incr(self._key(key))
        return result

    async def expire(self, key: str, seconds: int) -> bool:
        result: int | bool = await self._redis.expire(self._key(key), seconds)
        return bool(result)

    # ── sorted-set ops (used by rate limiter) ──────────────────────────────

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        result: int = await self._redis.zadd(self._key(key), mapping)
        return result

    async def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        result: int = await self._redis.zremrangebyscore(self._key(key), min_score, max_score)
        return result

    async def zcard(self, key: str) -> int:
        result: int = await self._redis.zcard(self._key(key))
        return result
