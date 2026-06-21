"""Unit tests for ConnectionPools lifecycle and readiness checks (fakes, no real I/O)."""

import pytest

from app.core.pools import ConnectionPools


class _FakePool:
    def __init__(self) -> None:
        self.closed = False
        self.pinged = False

    async def close(self) -> None:
        self.closed = True

    async def ping(self) -> None:
        self.pinged = True


async def test_startup_creates_and_shutdown_closes_pools() -> None:
    pg, redis, http = _FakePool(), _FakePool(), _FakePool()
    pools = ConnectionPools(
        pg_factory=_const(pg), redis_factory=_const(redis), http_factory=_const(http)
    )

    await pools.startup()
    assert pools.postgres is pg
    assert pools.redis is redis

    await pools.shutdown()
    assert pg.closed and http.closed


async def test_health_checks_pass_when_pools_respond() -> None:
    pools = ConnectionPools(
        pg_factory=_const(_FakePool()),
        redis_factory=_const(_FakePool()),
        http_factory=_const(_FakePool()),
        pg_ping=_ok,
        redis_ping=_ok,
    )
    await pools.startup()
    checks = {hc.name: hc for hc in pools.health_checks()}
    assert set(checks) == {"postgres", "redis"}
    await checks["postgres"].check()  # must not raise
    await checks["redis"].check()


async def test_postgres_health_check_raises_when_ping_fails() -> None:
    async def boom(_pool: object) -> None:
        raise RuntimeError("db down")

    pools = ConnectionPools(
        pg_factory=_const(_FakePool()),
        redis_factory=_const(_FakePool()),
        http_factory=_const(_FakePool()),
        pg_ping=boom,
        redis_ping=_ok,
    )
    await pools.startup()
    checks = {hc.name: hc for hc in pools.health_checks()}
    with pytest.raises(RuntimeError):
        await checks["postgres"].check()


def _const(value: object):
    async def factory() -> object:
        return value

    return factory


async def _ok(_pool: object) -> None:
    return None
