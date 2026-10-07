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

import contextlib

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
    "goal_connector_usage",
)
_GRANT_ONLY = ("goals", "cost_ledger")  # shared by other suites: granted, never emptied


def _ctx(tid: str) -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.PROFESSIONAL, api_key_id="k")


@contextlib.asynccontextmanager
async def _app_db_on(pg_url: str) -> AsyncIterator[Any]:
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
        for table in _TABLES + _GRANT_ONLY:
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
async def app_db(pg_url: str) -> AsyncIterator[Any]:
    """Session factory on a fresh NOBYPASSRLS role (what production runs as)."""
    async with _app_db_on(pg_url) as factory:
        yield factory


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


# ── SECRET-01: connector secrets are durable in Postgres ─────────────────────


def _secret_store(db: Any, redis: Any) -> Any:
    from app.mcp.connector_secrets import DurableConnectorSecretStore

    return DurableConnectorSecretStore(db_factory=db, redis=redis)


async def _tenant_rows(db: Any, tid: str, sql: str) -> list[Any]:
    async with db() as s, s.begin():
        await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tid})
        return list((await s.execute(text(sql), {"t": tid})).fetchall())


@pytest.mark.asyncio
async def test_secret_survives_flushall_and_is_encrypted(app_db: Any, redis: Any) -> None:
    await _mark_backfilled(app_db)
    tid = f"t-{uuid.uuid4().hex[:8]}"
    ref = "vault://connectors/builtin-github:work/token"
    await _secret_store(app_db, redis).store(ref, "ghp_plain_secret", tenant_ctx=_ctx(tid))

    await redis.flushall()

    fresh = _secret_store(app_db, redis)
    assert await fresh.resolve(ref, tenant_ctx=_ctx(tid)) == "ghp_plain_secret"
    rows = await _tenant_rows(
        app_db,
        tid,
        "SELECT server_id, secret_key, encrypted_value FROM mcp_credentials "
        "WHERE tenant_id = :t",
    )
    assert len(rows) == 1
    assert rows[0][0] == "builtin-github:work" and rows[0][1] == "token"
    assert "ghp_plain_secret" not in rows[0][2]


@pytest.mark.asyncio
async def test_secret_is_tenant_scoped(app_db: Any, redis: Any) -> None:
    await _mark_backfilled(app_db)
    a, b = f"ta-{uuid.uuid4().hex[:6]}", f"tb-{uuid.uuid4().hex[:6]}"
    ref = "vault://connectors/srv1/api_key"
    store = _secret_store(app_db, redis)
    await store.store(ref, "secret-a", tenant_ctx=_ctx(a))
    assert await store.resolve(ref, tenant_ctx=_ctx(b)) is None
    await redis.flushall()
    assert await store.resolve(ref, tenant_ctx=_ctx(b)) is None
    assert await store.resolve(ref, tenant_ctx=_ctx(a)) == "secret-a"


@pytest.mark.asyncio
async def test_secret_uses_tenant_envelope_key(app_db: Any, redis: Any, pg_url: str) -> None:
    from app.providers.tenant_vault import store_tenant_vault_key

    await _mark_backfilled(app_db)
    tid = f"t-{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(pg_url)
    async with admin.begin() as conn:  # tenant_vault_keys references tenants
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:id, :id, :email, 'free', true)"
            ),
            {"id": tid, "email": f"{tid}@example.test"},
        )
    await admin.dispose()
    await store_tenant_vault_key(app_db, tid, secrets.token_bytes(32))
    ref = "vault://connectors/srv/token"
    await _secret_store(app_db, redis).store(ref, "tenant-keyed", tenant_ctx=_ctx(tid))
    rows = await _tenant_rows(
        app_db, tid, "SELECT encrypted_value FROM mcp_credentials WHERE tenant_id = :t"
    )
    assert rows[0][0].startswith("tv1:")
    await redis.flushall()
    fresh = _secret_store(app_db, redis)
    assert await fresh.resolve(ref, tenant_ctx=_ctx(tid)) == "tenant-keyed"


@pytest.mark.asyncio
async def test_legacy_redis_secrets_are_backfilled_and_read_repaired(
    app_db: Any, redis: Any
) -> None:
    from app.mcp.connector_backfill import backfill_connectors_from_redis
    from app.providers.vault import RedisConnectorSecretStore, get_vault

    tid = f"t-{uuid.uuid4().hex[:8]}"
    legacy = RedisConnectorSecretStore(redis=redis, vault=get_vault())
    await legacy.store("vault://connectors/a:b/token", "legacy-1", tenant_ctx=_ctx(tid))
    await legacy.store("vault://connectors/c/password", "legacy-2", tenant_ctx=_ctx(tid))

    # Deploy window (no backfill recorded yet): served from the legacy key.
    store = _secret_store(app_db, redis)
    assert await store.resolve("vault://connectors/a:b/token", tenant_ctx=_ctx(tid)) == "legacy-1"

    keys_before = sorted(await redis.keys("mcp:connector_secrets:*"))
    report = await backfill_connectors_from_redis(redis, app_db)
    assert report["status"] == "complete", report
    assert report["secrets"]["copied"] + report["secrets"]["present"] == 2
    assert sorted(await redis.keys("mcp:connector_secrets:*")) == keys_before
    again = await backfill_connectors_from_redis(redis, app_db)
    assert again["secrets"]["copied"] == 0

    await redis.flushall()
    fresh = _secret_store(app_db, redis)
    assert await fresh.resolve("vault://connectors/c/password", tenant_ctx=_ctx(tid)) == "legacy-2"
    assert await fresh.resolve("vault://connectors/a:b/token", tenant_ctx=_ctx(tid)) == "legacy-1"


@pytest.mark.asyncio
async def test_rotation_reencrypts_postgres_connector_secrets(redis: Any, pg_url: str) -> None:
    # A full rotation walks EVERY tenant's secrets in every store; in the shared
    # database other tests' values (under other keys) made it report "failed".
    from tests._test_backends import fresh_migrated_database

    with fresh_migrated_database(pg_url) as isolated_url:
        async with _app_db_on(isolated_url) as app_db:
            await _rotation_reencrypts(app_db, redis, isolated_url)


async def _rotation_reencrypts(app_db: Any, redis: Any, pg_url: str) -> None:
    from app.providers.vault import CredentialVault, get_vault, rotate_master_key

    await _mark_backfilled(app_db)
    tid = f"t-{uuid.uuid4().hex[:8]}"
    ref = "vault://connectors/srv/token"
    await _secret_store(app_db, redis).store(ref, "rotate-me", tenant_ctx=_ctx(tid))
    new = CredentialVault(master_key="n" * 40)
    admin = create_async_engine(pg_url)
    async with admin.begin() as conn:  # rotation walks the tenants table
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:id, :id, :email, 'free', true)"
            ),
            {"id": tid, "email": f"{tid}@example.test"},
        )
    try:
        result = await rotate_master_key(
            old=get_vault(),
            new=new,
            redis=redis,
            system_db=async_sessionmaker(admin, expire_on_commit=False),
        )
        assert result["status"] == "complete", result
        assert result["stores"]["connector_secrets_pg"]["rotated"] >= 1
        async with admin.begin() as conn:
            value = (
                await conn.execute(
                    text("SELECT encrypted_value FROM mcp_credentials WHERE tenant_id = :t"),
                    {"t": tid},
                )
            ).scalar_one()
    finally:
        await admin.dispose()
    assert new.decrypt(value) == "rotate-me"


# ── MCPREG-03: connector usage is an exact-match, indexed lookup ─────────────


@pytest.mark.asyncio
async def test_connector_usage_exact_match_rls_and_index(app_db: Any, pg_url: str) -> None:
    from app.mcp.connector_usage import connector_usage, record_goal_connector_usage

    tid, other = f"t-{uuid.uuid4().hex[:8]}", f"o-{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(pg_url)
    async with admin.begin() as conn:
        for t in (tid, other):
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, :id, :email, 'free', true)"
                ),
                {"id": t, "email": f"{t}@example.test"},
            )
        for gid, t, st in (
            ("g1", tid, "complete"),
            ("g2", tid, "failed"),
            ("g3", other, "complete"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO goals (id, tenant_id, goal_text, status, priority, "
                    "autonomy_mode, workflow_mode, execution_context, dry_run, iterations) "
                    "VALUES (:id, :t, 'use it', :st, 'normal', "
                    "'bounded-autonomous', 'single_agent', '{}', false, 1)"
                ),
                {"id": f"{gid}-{tid}", "t": t, "st": st},
            )
    await admin.dispose()

    await record_goal_connector_usage(app_db, tid, "builtin-github:work-org", f"g1-{tid}")
    await record_goal_connector_usage(app_db, tid, "builtin-github:work-org", f"g1-{tid}")  # dup
    await record_goal_connector_usage(app_db, tid, "builtin-github:work-org", f"g2-{tid}")
    await record_goal_connector_usage(app_db, tid, "builtin-github", f"g2-{tid}")
    await record_goal_connector_usage(app_db, other, "builtin-github:work-org", f"g3-{tid}")

    work = await connector_usage(app_db, tid, "builtin-github:work-org", limit=10)
    assert work["total"] == 2 and work["success_count"] == 1
    assert {g["id"] for g in work["goals"]} == {f"g1-{tid}", f"g2-{tid}"}
    # The canonical id does NOT substring-match the multi-connection id.
    canonical = await connector_usage(app_db, tid, "builtin-github", limit=10)
    assert canonical["total"] == 1
    assert (await connector_usage(app_db, tid, "github", limit=10))["total"] == 0

    async with app_db() as s, s.begin():
        await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tid})
        await s.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            r[0]
            for r in (
                await s.execute(
                    text(
                        "EXPLAIN SELECT goal_id FROM goal_connector_usage "
                        "WHERE tenant_id = :t AND connector_id = :c "
                        "ORDER BY first_used_at DESC LIMIT 20"
                    ),
                    {"t": tid, "c": "builtin-github"},
                )
            ).fetchall()
        )
    assert "ix_goal_connector_usage_lookup" in plan, plan

    # MCPClient records usage for the goal whose run is executing the call.
    from types import SimpleNamespace

    from app.mcp.client import MCPClient
    from app.providers.guarded_completion import goal_charge_scope

    client = MCPClient(MCPRegistry(None, db_factory=app_db))
    with goal_charge_scope(None, SimpleNamespace(goal_id=f"g2-{tid}"), _ctx(tid)):
        await client._record_goal_usage("slack-main", tid)
    await client._record_goal_usage("slack-main", tid)  # no goal running: nothing
    slack = await connector_usage(app_db, tid, "slack-main", limit=10)
    assert [g["id"] for g in slack["goals"]] == [f"g2-{tid}"]


# ── MCPREG-05: erasing a connector's secrets ─────────────────────────────────


@pytest.mark.asyncio
async def test_delete_server_erases_rows_cache_and_legacy_keys(app_db: Any, redis: Any) -> None:
    await _mark_backfilled(app_db)
    tid = f"t-{uuid.uuid4().hex[:8]}"
    store = _secret_store(app_db, redis)
    await store.store("vault://connectors/gone:x/token", "a", tenant_ctx=_ctx(tid))
    await store.store("vault://connectors/gone:x/password", "b", tenant_ctx=_ctx(tid))
    await store.store("vault://connectors/kept/token", "c", tenant_ctx=_ctx(tid))
    await store.resolve("vault://connectors/gone:x/token", tenant_ctx=_ctx(tid))  # cached
    await redis.set(f"mcp:connector_secrets:{tid}:gone:x:legacy_only", "ct")

    assert await store.delete_server("gone:x", tenant_ctx=_ctx(tid)) == 2

    rows = await _tenant_rows(
        app_db, tid, "SELECT server_id FROM mcp_credentials WHERE tenant_id = :t"
    )
    assert [r[0] for r in rows] == ["kept"]
    assert await redis.keys(f"mcp:*:{tid}:gone:x:*") == []
    assert await store.resolve("vault://connectors/kept/token", tenant_ctx=_ctx(tid)) == "c"
