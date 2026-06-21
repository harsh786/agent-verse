"""Integration test: three-layer tenant isolation (Postgres + Redis + app layer).

Proves that tenant A cannot read tenant B's data through any layer.
Requires real containers — run with: uv run pytest -m integration
"""

from __future__ import annotations

import hashlib
import secrets

import pytest

pytestmark = pytest.mark.integration


async def test_tenant_isolation_across_all_three_layers() -> None:
    """Tenant A's resources are invisible to Tenant B at every isolation layer."""
    import asyncpg
    import redis.asyncio as aioredis
    from testcontainers.postgres import PostgresContainer
    from testcontainers.redis import RedisContainer

    with (
        PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg,
        RedisContainer("redis:7-alpine") as redis_container,
    ):
        # ── Layer 3: Postgres RLS ──────────────────────────────────────────
        conn_url = pg.get_connection_url().replace("postgresql+asyncpg://", "")
        pool = await asyncpg.create_pool(f"postgresql://{conn_url}")

        # Apply baseline + tenancy migrations
        await pool.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await pool.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        await pool.execute("""
            CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS text AS $$
              SELECT current_setting('app.tenant_id', true);
            $$ LANGUAGE sql STABLE;
        """)
        await pool.execute("""
            CREATE TABLE tenants (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                plan_tier TEXT NOT NULL DEFAULT 'free',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await pool.execute("""
            CREATE TABLE api_keys (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                key_hash TEXT UNIQUE NOT NULL,
                scopes TEXT[] NOT NULL DEFAULT '{}',
                expires_at TIMESTAMPTZ,
                last_used_at TIMESTAMPTZ,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await pool.execute("ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY")
        await pool.execute("""
            CREATE POLICY api_keys_tenant_isolation ON api_keys
            USING (tenant_id = current_setting('app.tenant_id', true))
        """)

        # Create two tenants and their keys
        await pool.execute(
            "INSERT INTO tenants(id, name, email) VALUES($1,$2,$3)",
            "tenant-A", "Alice Corp", "alice@example.com",
        )
        await pool.execute(
            "INSERT INTO tenants(id, name, email) VALUES($1,$2,$3)",
            "tenant-B", "Bob Corp", "bob@example.com",
        )
        key_a = secrets.token_urlsafe(32)
        key_b = secrets.token_urlsafe(32)
        hash_a = hashlib.sha256(key_a.encode()).hexdigest()
        hash_b = hashlib.sha256(key_b.encode()).hexdigest()
        await pool.execute(
            "INSERT INTO api_keys(id, tenant_id, name, key_hash) VALUES($1,$2,$3,$4)",
            "kid-a", "tenant-A", "A Key", hash_a,
        )
        await pool.execute(
            "INSERT INTO api_keys(id, tenant_id, name, key_hash) VALUES($1,$2,$3,$4)",
            "kid-b", "tenant-B", "B Key", hash_b,
        )

        # Verify RLS: tenant A cannot see tenant B's keys
        async with pool.acquire() as conn:
            await conn.execute("SET app.tenant_id = 'tenant-A'")
            rows = await conn.fetch("SELECT id FROM api_keys")
            key_ids = {r["id"] for r in rows}
            assert "kid-a" in key_ids, "Tenant A should see own key"
            assert "kid-b" not in key_ids, "Tenant A must NOT see tenant B's key"

        async with pool.acquire() as conn:
            await conn.execute("SET app.tenant_id = 'tenant-B'")
            rows = await conn.fetch("SELECT id FROM api_keys")
            key_ids = {r["id"] for r in rows}
            assert "kid-b" in key_ids
            assert "kid-a" not in key_ids

        await pool.close()

        # ── Layer 2: Redis key prefixing ──────────────────────────────────
        redis_url = f"redis://{redis_container.get_container_host_ip()}:{redis_container.get_exposed_port(6379)}/0"
        redis_client = aioredis.from_url(redis_url)

        from app.tenancy.store import TenantScopedStore

        store_a = TenantScopedStore(redis_client, "tenant-A")
        store_b = TenantScopedStore(redis_client, "tenant-B")

        await store_a.set("secret", "A-value")
        await store_b.set("secret", "B-value")

        assert await store_a.get("secret") == "A-value"
        assert await store_b.get("secret") == "B-value"

        # Verify raw Redis keys include tenant prefix
        all_keys = [k.decode() for k in await redis_client.keys("*")]
        assert "tenant:tenant-A:secret" in all_keys
        assert "tenant:tenant-B:secret" in all_keys
        assert "secret" not in all_keys

        await redis_client.aclose()

        # ── Layer 1: FastAPI middleware ────────────────────────────────────
        # Verified by test_middleware.py unit tests (fake resolver returns
        # TenantContext only for the correct key — no bleed between tenants).
