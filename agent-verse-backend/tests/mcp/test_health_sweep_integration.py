"""a02-F034-N1: the health sweep reads connectors from Postgres ``mcp_servers``.

Real Postgres (migrated) + real Redis. Connectors are created through the durable
registry under the least-privilege app role (what the API does since MCPREG-01,
which never writes the legacy ``mcp:servers:*`` keys); the sweep reads them
cross-tenant through a BYPASSRLS maintenance role in keyset pages. Under a
NOBYPASSRLS role the read fails loudly instead of silently seeing nothing.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/mcp/test_health_sweep_integration.py -q -m integration
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.mcp.health_sweep import fetch_connector_page, run_health_sweep
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration

_TABLES = ("mcp_servers", "mcp_builtin_provisioning", "connector_store_backfills")


def _ctx(tid: str) -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.PROFESSIONAL, api_key_id="k")


async def _role_factory(pg_url: str, *, bypass: bool) -> tuple[Any, Any]:
    password = secrets.token_urlsafe(24)
    role = f"test_hs_{'mnt' if bypass else 'app'}_{secrets.token_hex(4)}"
    admin = create_async_engine(pg_url)
    async with admin.begin() as conn:
        quoted = (await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})).scalar()
        rls = "BYPASSRLS" if bypass else "NOBYPASSRLS"
        await conn.execute(
            text(f"CREATE ROLE {role} LOGIN PASSWORD {quoted} NOSUPERUSER {rls}")
        )
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        for table in _TABLES:
            await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role}"))
    await admin.dispose()
    url = make_url(pg_url).set(username=role, password=password)
    engine = create_async_engine(url.render_as_string(hide_password=False))
    return async_sessionmaker(engine, expire_on_commit=False), engine


@pytest_asyncio.fixture
async def dbs(pg_url: str) -> AsyncIterator[tuple[Any, Any]]:
    admin = create_async_engine(pg_url)
    async with admin.begin() as conn:
        for table in _TABLES:
            await conn.execute(text(f"DELETE FROM {table}"))
    await admin.dispose()
    app_db, app_engine = await _role_factory(pg_url, bypass=False)
    mnt_db, mnt_engine = await _role_factory(pg_url, bypass=True)
    try:
        yield app_db, mnt_db
    finally:
        await app_engine.dispose()
        await mnt_engine.dispose()


@pytest_asyncio.fixture
async def redis(redis_url: str) -> AsyncIterator[Any]:
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url, decode_responses=True)
    await client.flushall()
    try:
        yield client
    finally:
        await client.aclose()


async def _seed(app_db: Any, redis: Any) -> set[tuple[str, str]]:
    registry = MCPRegistry(redis, db_factory=app_db)
    expected: set[tuple[str, str]] = set()
    for tid in ("tenant-a", "tenant-b"):
        for i in range(3):
            sid = await registry.register(
                MCPServerConfig(name=f"{tid}-c{i}", url=f"https://c{i}.{tid}.example.com"),
                tenant_ctx=_ctx(tid),
            )
            expected.add((tid, sid))
    # A disabled connector is not probed.
    off = await registry.register(
        MCPServerConfig(name="off", url="https://off.example.com"), tenant_ctx=_ctx("tenant-a")
    )
    async with app_db() as s, s.begin():
        await s.execute(text("SELECT set_config('app.tenant_id', 'tenant-a', true)"))
        await s.execute(
            text("UPDATE mcp_servers SET enabled = false WHERE tenant_id = 'tenant-a' AND id = :i"),
            {"i": off},
        )
    return expected


async def test_sweep_probes_every_registry_connector_across_tenants(
    dbs: tuple[Any, Any], redis: Any
) -> None:
    app_db, mnt_db = dbs
    expected = await _seed(app_db, redis)
    assert await redis.keys("mcp:servers:*") == []  # nothing for the old scan to find

    probed: list[str] = []

    async def _probe(cfg: Any) -> dict[str, Any]:
        probed.append(cfg.url)
        return {"status": "healthy", "latency_ms": 1, "error": None}

    written: list[dict[str, Any]] = []

    async def _persist(snaps: list[dict[str, Any]]) -> int:
        written.extend(snaps)
        return len(snaps)

    out = await run_health_sweep(
        factory=mnt_db, redis=redis, persist=_persist, probe=_probe, page_size=2
    )
    assert out["servers_checked"] == 6 and out["completed_pass"] is True
    assert {(s["tenant_id"], s["server_id"]) for s in written} == expected
    assert not any("off.example.com" in u for u in probed)


async def test_keyset_pages_are_bounded_and_ordered(dbs: tuple[Any, Any], redis: Any) -> None:
    app_db, mnt_db = dbs
    expected = await _seed(app_db, redis)
    seen: list[tuple[str, str]] = []
    after: tuple[str, str] | None = None
    while True:
        page = await fetch_connector_page(mnt_db, after, 4)
        assert len(page) <= 4
        seen.extend((t, s) for t, s, _ in page)
        if len(page) < 4:
            break
        after = (page[-1][0], page[-1][1])
    assert seen == sorted(seen) and set(seen) == expected


async def test_without_bypassrls_the_read_fails_loudly(dbs: tuple[Any, Any], redis: Any) -> None:
    app_db, _ = dbs
    await _seed(app_db, redis)
    with pytest.raises(Exception, match="row-level security"):
        await fetch_connector_page(app_db, None, 10)
