"""Centralized connection pools (Postgres / Redis / httpx).

Lifecycle is owned by the FastAPI lifespan: ``startup()`` builds the pools, ``shutdown()``
closes them. Each backend contributes a readiness :class:`HealthCheck` so ``/health``
reflects real connectivity. Factories and ping functions are injectable for deterministic
unit tests; the defaults build real pools sized per the platform's connection rules
(asyncpg 5-20, Redis 50, httpx 100/20-keepalive).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import Settings, get_settings
from app.observability.health import HealthCheck

PoolFactory = Callable[[], Awaitable[Any]]
PingFn = Callable[[Any], Awaitable[None]]


async def _default_pg_factory(settings: Settings) -> Any:
    import asyncpg

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.create_pool(dsn, min_size=5, max_size=20)


async def _default_redis_factory(settings: Settings) -> Any:
    import redis.asyncio as aioredis

    return aioredis.from_url(
        settings.redis_url, max_connections=50, retry_on_timeout=True, decode_responses=True
    )


async def _default_http_factory() -> Any:
    import httpx

    limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
    return httpx.AsyncClient(limits=limits, timeout=httpx.Timeout(30.0))


async def _default_pg_ping(pool: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute("SELECT 1")


async def _default_redis_ping(pool: Any) -> None:
    await pool.ping()


class ConnectionPools:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        pg_factory: PoolFactory | None = None,
        redis_factory: PoolFactory | None = None,
        http_factory: PoolFactory | None = None,
        pg_ping: PingFn = _default_pg_ping,
        redis_ping: PingFn = _default_redis_ping,
    ) -> None:
        self._settings = settings or get_settings()
        self._pg_factory = pg_factory or (lambda: _default_pg_factory(self._settings))
        self._redis_factory = redis_factory or (lambda: _default_redis_factory(self._settings))
        self._http_factory = http_factory or _default_http_factory
        self._pg_ping = pg_ping
        self._redis_ping = redis_ping
        self.postgres: Any = None
        self.redis: Any = None
        self.http: Any = None

    async def startup(self) -> None:
        self.postgres = await self._pg_factory()
        self.redis = await self._redis_factory()
        self.http = await self._http_factory()

    async def shutdown(self) -> None:
        for pool, method in (
            (self.postgres, "close"),
            (self.redis, "aclose"),
            (self.http, "close"),
        ):
            closer = getattr(pool, method, None) if pool is not None else None
            if closer is not None:
                await closer()

    def health_checks(self) -> list[HealthCheck]:
        return [
            HealthCheck(name="postgres", check=lambda: self._pg_ping(self.postgres)),
            HealthCheck(name="redis", check=lambda: self._redis_ping(self.redis)),
        ]
