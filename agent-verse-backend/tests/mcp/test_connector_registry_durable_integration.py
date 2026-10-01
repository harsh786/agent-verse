"""MCPREG-01: the connector registry is durable in Postgres; Redis is only a cache.

Real Postgres (migrated, least-privilege NOBYPASSRLS role) + real Redis. A
FLUSHALL loses nothing; RLS keeps tenants apart; the one-time backfill copies
the legacy Redis-only store idempotently and never deletes Redis keys.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/mcp/test_connector_registry_durable_integration.py -q -m integration
"""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.mcp.connector_store import ConnectorConflictError
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration

_TABLES = (
    "mcp_servers",
    "mcp_credentials",
    "mcp_builtin_provisioning",
    "connector_store_backfills",
    "tenant_vault_keys",
)


def _ctx(tid: str) -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.PROFESSIONAL, api_key_id="k")


@pytest_asyncio.fixture
async def app_db(pg_url: str) -> AsyncIterator[Any]:
    """Session factory on a fresh NOBYPASSRLS role (what production runs as)."""
    password = secrets.token_urlsafe(24)
    role = f"test_app_conn_{secrets.token_hex(4)}"
    admin = create_async_engine(pg_url)
    async with admin.begin() as conn:
        quoted = (await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})).scalar()
        await conn.execute(
            text(
                f"CREATE ROLE {role} LOGIN PASSWORD {quoted} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        for table in _TABLES:
            await conn.execute(
                text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role}")
            )
            await conn.execute(text(f"DELETE FROM {table}"))  # superuser: RLS bypassed
    url = make_url(pg_url).set(username=role, password=password)
    engine = create_async_engine(url.render_as_string(hide_password=False))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        await admin.dispose()


@pytest_asyncio.fixture
async def redis(redis_url: str) -> AsyncIterator[Any]:
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url, decode_responses=True)
    await client.flushall()
    try:
        yield client
    finally:
        await client.aclose()


async def _mark_backfilled(db: Any) -> None:
    async with db() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO connector_store_backfills (name) VALUES "
                "('connector_redis_to_postgres_v1') ON CONFLICT DO NOTHING"
            )
        )


def _cfg(name: str, **kw: Any) -> MCPServerConfig:
    return MCPServerConfig(name=name, url="https://mcp.example.com/mcp", **kw)


@pytest.mark.asyncio
async def test_registry_survives_redis_flushall(app_db: Any, redis: Any) -> None:
    await _mark_backfilled(app_db)
    tid = f"t-{uuid.uuid4().hex[:8]}"
    reg = MCPRegistry(redis, db_factory=app_db)
    sid = await reg.register(
        _cfg("Orders API", auth_config={"token": "vault://connectors/x/token"}),
        tenant_ctx=_ctx(tid),
    )
    assert (await reg.get(sid, tenant_ctx=_ctx(tid))) is not None  # also fills the cache

    await redis.flushall()

    fresh = MCPRegistry(redis, db_factory=app_db)  # another replica / a restart
    got = await fresh.get(sid, tenant_ctx=_ctx(tid))
    assert got is not None and got.name == "Orders API"
    assert got.auth_config == {"token": "vault://connectors/x/token"}
    assert [s for s, _ in await fresh.list_server_records(tenant_ctx=_ctx(tid))] == [sid]


@pytest.mark.asyncio
async def test_rls_isolates_tenants(app_db: Any, redis: Any) -> None:
    await _mark_backfilled(app_db)
    a, b = f"ta-{uuid.uuid4().hex[:6]}", f"tb-{uuid.uuid4().hex[:6]}"
    reg = MCPRegistry(redis, db_factory=app_db)
    sid = await reg.register(_cfg("Shared Name"), tenant_ctx=_ctx(a))
    assert await reg.get(sid, tenant_ctx=_ctx(b)) is None
    assert await reg.list_server_records(tenant_ctx=_ctx(b)) == []
    # The same display name is fine in another tenant.
    await reg.register(_cfg("Shared Name"), tenant_ctx=_ctx(b))
    # Without the tenant GUC the app role sees nothing at all.
    async with app_db() as s, s.begin():
        assert (await s.execute(text("SELECT count(*) FROM mcp_servers"))).scalar() == 0
        with pytest.raises(Exception, match="row-level security"):
            await s.execute(
                text(
                    "INSERT INTO mcp_servers (tenant_id, id, name, name_key, config) "
                    "VALUES (:t, 'x', 'n', 'n', '{}'::jsonb)"
                ),
                {"t": a},
            )


@pytest.mark.asyncio
async def test_update_and_unregister_persist_and_invalidate_cache(
    app_db: Any, redis: Any
) -> None:
    await _mark_backfilled(app_db)
    tid = f"t-{uuid.uuid4().hex[:8]}"
    reg_a = MCPRegistry(redis, db_factory=app_db)
    reg_b = MCPRegistry(redis, db_factory=app_db)  # second replica, same Redis
    sid = await reg_a.register(_cfg("Before"), tenant_ctx=_ctx(tid))
    assert (await reg_b.get(sid, tenant_ctx=_ctx(tid))).name == "Before"  # cached now

    updated = _cfg("After", server_id=sid)
    assert await reg_a.update(sid, updated, tenant_ctx=_ctx(tid)) is True
    assert (await reg_b.get(sid, tenant_ctx=_ctx(tid))).name == "After"

    assert await reg_a.unregister(sid, tenant_ctx=_ctx(tid)) is True
    assert await reg_b.get(sid, tenant_ctx=_ctx(tid)) is None
    assert await reg_a.unregister(sid, tenant_ctx=_ctx(tid)) is False


@pytest.mark.asyncio
async def test_name_unique_per_tenant(app_db: Any, redis: Any) -> None:
    await _mark_backfilled(app_db)
    tid = f"t-{uuid.uuid4().hex[:8]}"
    reg = MCPRegistry(redis, db_factory=app_db)
    await reg.register(_cfg("Jira  Cloud"), tenant_ctx=_ctx(tid))
    with pytest.raises(ConnectorConflictError) as info:
        await reg.register(_cfg("jira cloud"), tenant_ctx=_ctx(tid))
    assert info.value.kind == "name"


@pytest.mark.asyncio
async def test_builtin_marker_is_durable(app_db: Any, redis: Any) -> None:
    """A built-in the tenant removed does not come back after a Redis flush."""
    await _mark_backfilled(app_db)
    tid = f"t-{uuid.uuid4().hex[:8]}"
    reg = MCPRegistry(redis, db_factory=app_db, auto_provision_builtins=True)
    records = await reg.list_server_records(tenant_ctx=_ctx(tid))
    assert records, "credential-free built-ins are provisioned"
    removed = records[0][0]
    assert await reg.unregister(removed, tenant_ctx=_ctx(tid))

    await redis.flushall()
    again = MCPRegistry(redis, db_factory=app_db, auto_provision_builtins=True)
    ids = {sid for sid, _ in await again.list_server_records(tenant_ctx=_ctx(tid))}
    assert removed not in ids
    assert len(ids) == len(records) - 1


@pytest.mark.asyncio
async def test_backfill_copies_legacy_redis_idempotently(app_db: Any, redis: Any) -> None:
    from app.mcp.connector_backfill import (
        backfill_connectors_from_redis,
        ensure_connector_backfill,
    )
    from app.mcp.connector_store import backfill_completed

    tid = f"t-{uuid.uuid4().hex[:8]}"
    legacy = MCPRegistry(redis)  # the old Redis-only registry
    sid_a = await legacy.register(_cfg("Legacy A"), tenant_ctx=_ctx(tid))
    sid_b = await legacy.register(
        _cfg("legacy a", server_id="builtin-github:work-org"), tenant_ctx=_ctx(tid)
    )  # duplicate name from before names were unique
    await redis.set(f"mcp:builtins_provisioned:{tid}", json.dumps({"fp": "x", "ids": ["a"]}))
    legacy_keys = sorted(await redis.keys("mcp:*"))

    report = await ensure_connector_backfill(redis, app_db)
    assert report["status"] == "complete", report
    assert report["servers"]["copied"] + report["servers"]["renamed"] == 2
    assert report["servers"]["renamed"] == 1
    assert report["builtin_markers"] == 1
    assert await backfill_completed(app_db)
    assert sorted(await redis.keys("mcp:*")) == legacy_keys  # nothing deleted

    again = await backfill_connectors_from_redis(redis, app_db)
    assert again["status"] == "complete"
    assert again["servers"]["copied"] == 0 and again["servers"]["present"] == 2
    assert (await ensure_connector_backfill(redis, app_db))["status"] == "already_complete"

    await redis.flushall()
    reg = MCPRegistry(redis, db_factory=app_db)
    names = {sid: cfg.name for sid, cfg in await reg.list_server_records(tenant_ctx=_ctx(tid))}
    assert set(names) == {sid_a, sid_b}
    # The duplicate is kept, under "<name> (<id>)", never dropped.
    assert sorted(names.values()) in (
        sorted(["Legacy A", f"legacy a ({sid_b})"]),
        sorted([f"Legacy A ({sid_a})", "legacy a"]),
    )


@pytest.mark.asyncio
async def test_read_repair_before_backfill_completes(app_db: Any, redis: Any) -> None:
    """Deploy window: a legacy Redis-only connector is still served and copied."""
    tid = f"t-{uuid.uuid4().hex[:8]}"
    sid = await MCPRegistry(redis).register(_cfg("Old One"), tenant_ctx=_ctx(tid))
    reg = MCPRegistry(redis, db_factory=app_db)
    assert [s for s, _ in await reg.list_server_records(tenant_ctx=_ctx(tid))] == [sid]
    await redis.flushall()  # Postgres now holds it
    assert (await reg.get(sid, tenant_ctx=_ctx(tid))).name == "Old One"


@pytest.mark.asyncio
async def test_unregister_removes_legacy_copy_so_it_cannot_resurrect(
    app_db: Any, redis: Any
) -> None:
    from app.mcp.connector_backfill import backfill_connectors_from_redis

    tid = f"t-{uuid.uuid4().hex[:8]}"
    sid = await MCPRegistry(redis).register(_cfg("Gone Soon"), tenant_ctx=_ctx(tid))
    await backfill_connectors_from_redis(redis, app_db)
    reg = MCPRegistry(redis, db_factory=app_db)
    assert await reg.unregister(sid, tenant_ctx=_ctx(tid))
    await backfill_connectors_from_redis(redis, app_db)  # re-run
    assert await reg.get(sid, tenant_ctx=_ctx(tid)) is None


@pytest.mark.asyncio
async def test_concurrent_same_name_creates_one_wins(app_db: Any, redis: Any) -> None:
    await _mark_backfilled(app_db)
    tid = f"t-{uuid.uuid4().hex[:8]}"
    reg = MCPRegistry(redis, db_factory=app_db)
    results = await asyncio.gather(
        reg.create(_cfg("Race"), tenant_ctx=_ctx(tid)),
        reg.create(_cfg("Race"), tenant_ctx=_ctx(tid)),
        return_exceptions=True,
    )
    assert sum(isinstance(r, str) for r in results) == 1
    assert sum(isinstance(r, ConnectorConflictError) for r in results) == 1
