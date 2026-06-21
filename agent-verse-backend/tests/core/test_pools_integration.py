"""Integration test: ConnectionPools against real Postgres + Redis (testcontainers).

Marked 'integration' so the fast unit loop can skip it; CI runs it with Docker available.
Proves the readiness checks succeed against live backends and pools close cleanly.
"""

import pytest

from app.core.config import Settings
from app.core.pools import ConnectionPools

pytestmark = pytest.mark.integration


async def test_readiness_checks_pass_against_live_backends() -> None:
    from testcontainers.postgres import PostgresContainer
    from testcontainers.redis import RedisContainer

    with (
        PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg,
        RedisContainer("redis:7-alpine") as redis,
    ):
        redis_url = f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"
        settings = Settings(database_url=pg.get_connection_url(), redis_url=redis_url)

        pools = ConnectionPools(settings=settings)
        await pools.startup()
        try:
            results = {}
            for hc in pools.health_checks():
                await hc.check()
                results[hc.name] = "up"
            assert results == {"postgres": "up", "redis": "up"}
        finally:
            await pools.shutdown()
