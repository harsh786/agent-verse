"""MDB-01 / TG-01 on real Postgres + Redis: no connection-string userinfo at rest.

A connector registered through the API stores its MongoDB URI ONLY as an
encrypted ``mcp_credentials`` row: the ``mcp_servers`` row (``url`` column and
``config`` JSON) and every Redis key hold no username or password. Rows stored
in clear by older releases are sealed by the idempotent migration.
"""

from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

URI = "mongodb://alice:S3cretPw@8.8.8.8:27017/shop?authSource=admin"
LEAKS = ("S3cretPw", "alice")
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
async def dbs(pg_url: str) -> AsyncIterator[tuple[Any, Any]]:
    """(app factory on a NOBYPASSRLS role, cross-tenant maintenance factory)."""
    password = secrets.token_urlsafe(24)
    role = f"test_dsn_{secrets.token_hex(4)}"
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
            await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role}"))
            await conn.execute(text(f"DELETE FROM {table}"))
        await conn.execute(
            text(
                "INSERT INTO connector_store_backfills (name) VALUES "
                "('connector_redis_to_postgres_v1') ON CONFLICT DO NOTHING"
            )
        )
    url = make_url(pg_url).set(username=role, password=password)
    engine = create_async_engine(url.render_as_string(hide_password=False))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False), async_sessionmaker(admin)
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


async def _redis_dump(redis: Any) -> str:
    out: list[str] = []
    async for key in redis.scan_iter(match="*"):
        kind = await redis.type(key)
        if kind == "string":
            out.append(f"{key}={await redis.get(key)}")
        elif kind == "set":
            out.append(f"{key}={sorted(await redis.smembers(key))}")
    return "\n".join(out)


async def _rows(admin_db: Any, sql: str, **params: Any) -> list[Any]:
    async with admin_db() as s, s.begin():
        return list((await s.execute(text(sql), params)).fetchall())


def _assert_clean(blob: str) -> None:
    for leak in LEAKS:
        assert leak not in blob, blob


async def test_api_registration_leaves_no_userinfo_at_rest(
    dbs: tuple[Any, Any], redis: Any
) -> None:
    from app.api.connectors import router as connectors_router
    from app.mcp.connector_secrets import DurableConnectorSecretStore
    from app.tenancy.middleware import TenantMiddleware

    app_db, admin_db = dbs
    tid = f"t-{uuid.uuid4().hex[:8]}"
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _ctx(tid) if key == "k" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(connectors_router)
    app.state.mcp_registry = MCPRegistry(redis, db_factory=app_db)
    store = DurableConnectorSecretStore(db_factory=app_db, redis=redis)
    app.state.connector_secret_store = store
    app.state.connector_secret_store_is_production_safe = True

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        created = await client.post(
            "/connectors",
            headers={"X-API-Key": "k"},
            json={
                "name": "orders-db",
                "type": "mongodb",
                "url": URI,
                "auth_type": "none",
                "auth_config": {"url": URI, "database": "shop"},
            },
        )
        assert created.status_code == 201, created.text
        sid = created.json()["server_id"]
        listed = await client.get("/connectors", headers={"X-API-Key": "k"})
        got = await client.get(f"/connectors/{sid}", headers={"X-API-Key": "k"})
    for resp in (created, listed, got):
        _assert_clean(resp.text)

    rows = await _rows(
        admin_db, "SELECT url, config::text FROM mcp_servers WHERE tenant_id = :t", t=tid
    )
    assert len(rows) == 1
    assert rows[0][0] == "builtin://"
    _assert_clean(rows[0][1])
    creds = await _rows(
        admin_db,
        "SELECT secret_key, encrypted_value FROM mcp_credentials WHERE tenant_id = :t",
        t=tid,
    )
    assert [c[0] for c in creds] == ["url"]
    _assert_clean(creds[0][1])
    _assert_clean(await _redis_dump(redis))
    assert await store.resolve(f"vault://connectors/{sid}/url", tenant_ctx=_ctx(tid)) == URI


async def test_migration_seals_legacy_plaintext_rows_idempotently(
    dbs: tuple[Any, Any], redis: Any
) -> None:
    from app.mcp.connector_secrets import DurableConnectorSecretStore
    from app.mcp.dsn_secret_migration import migrate_plaintext_dsn_connectors

    app_db, admin_db = dbs
    tids = [f"t-{uuid.uuid4().hex[:8]}" for _ in range(3)]
    reg = MCPRegistry(redis, db_factory=app_db)
    legacy = {}
    for tid in tids:
        # What an older release stored: the URI in BOTH places, in clear.
        cfg = MCPServerConfig(
            server_id="builtin-mongodb:orders-db",
            name="orders-db",
            url=URI,
            auth_config={"url": URI, "database": "shop"},
            builtin_type="builtin-mongodb",
        )
        await reg.register(cfg, tenant_ctx=_ctx(tid))
        await reg.get(cfg.server_id, tenant_ctx=_ctx(tid))  # fills the read cache
        legacy[tid] = cfg
    # A legacy Redis-only copy as well.
    await redis.set(f"mcp:servers:{tids[0]}:builtin-mongodb:old", legacy[tids[0]].model_dump_json())
    # A connector without any connection string is never touched.
    await reg.register(
        MCPServerConfig(server_id="plain", name="plain", url="https://api.example.com"),
        tenant_ctx=_ctx(tids[0]),
    )
    before = await _rows(admin_db, "SELECT config::text FROM mcp_servers WHERE id = 'plain'")

    store = DurableConnectorSecretStore(db_factory=app_db, redis=redis)
    report = await migrate_plaintext_dsn_connectors(
        db_factory=app_db, scan_db_factory=admin_db, secret_store=store, redis=redis, batch_size=2
    )

    assert report["errors"] == [], report
    assert report["postgres_sealed"] == 3
    assert report["redis_sealed"] == 1
    for row in await _rows(admin_db, "SELECT url, config::text FROM mcp_servers"):
        _assert_clean(row[0])
        _assert_clean(row[1])
    _assert_clean(await _redis_dump(redis))
    assert (
        await _rows(admin_db, "SELECT config::text FROM mcp_servers WHERE id = 'plain'") == before
    )
    for tid in tids:
        cfg = await MCPRegistry(redis, db_factory=app_db).get(
            "builtin-mongodb:orders-db", tenant_ctx=_ctx(tid)
        )
        assert cfg is not None and cfg.url == "builtin://"
        assert cfg.display_url == "mongodb://8.8.8.8:27017/shop?authSource=admin"
        ref = cfg.auth_config["url"]
        assert await store.resolve(ref, tenant_ctx=_ctx(tid)) == URI
    legacy_raw = await redis.get(f"mcp:servers:{tids[0]}:builtin-mongodb:old")
    assert json.loads(legacy_raw)["auth_config"]["url"].startswith("vault://connectors/")

    again = await migrate_plaintext_dsn_connectors(
        db_factory=app_db, scan_db_factory=admin_db, secret_store=store, redis=redis
    )
    assert again["postgres_sealed"] == again["redis_sealed"] == 0
    assert again["errors"] == []
