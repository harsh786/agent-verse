"""Million-doc RAG T1 — chunk-table partitioning design proof.

Validates that HASH-partitioning knowledge chunks by ``tenant_id`` gives us
(a) correct row routing, (b) partition pruning for single-tenant queries (so a
query scans one partition, not the whole corpus — the point of partitioning at
10M+ chunks), and (c) RLS tenant isolation that still holds on a partitioned
table under a NON-superuser role.

This proves the design on a fresh partitioned table. Retrofitting the existing
populated per-dimension tables is an ONLINE-MIGRATION OPS TASK (see
docs/plans/RAG_MILLION_DOCS_PLAN.md) — a naive alembic copy would rebuild HNSW
on the very tables that are huge, so it is intentionally not shipped as an
in-place migration. Marked integration.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_hash_partitioning_routes_prunes_and_isolates() -> None:
    import asyncpg
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        raw = pg.get_connection_url().replace("postgresql+asyncpg://", "")
        pool = await asyncpg.create_pool(f"postgresql://{raw}")
        await pool.execute("CREATE EXTENSION IF NOT EXISTS vector")

        # Partitioned parent + 4 hash partitions.
        await pool.execute(
            """
            CREATE TABLE kchunks (
                tenant_id text NOT NULL,
                id text NOT NULL,
                collection_id text NOT NULL,
                content text NOT NULL,
                embedding vector(8),
                PRIMARY KEY (tenant_id, id)
            ) PARTITION BY HASH (tenant_id)
            """
        )
        for i in range(4):
            await pool.execute(
                f"CREATE TABLE kchunks_p{i} PARTITION OF kchunks "
                f"FOR VALUES WITH (MODULUS 4, REMAINDER {i})"
            )

        # RLS on the parent propagates to partitions.
        await pool.execute("ALTER TABLE kchunks ENABLE ROW LEVEL SECURITY")
        await pool.execute("ALTER TABLE kchunks FORCE ROW LEVEL SECURITY")
        await pool.execute(
            "CREATE POLICY kchunks_iso ON kchunks "
            "USING (tenant_id = current_setting('app.tenant_id', true))"
        )

        tenants = [f"tenant-{n}" for n in range(6)]
        for t in tenants:
            for j in range(5):
                await pool.execute(
                    "INSERT INTO kchunks(tenant_id, id, collection_id, content, embedding) "
                    "VALUES($1,$2,$3,$4,$5::vector)",
                    t, f"{t}-c{j}", "col", f"doc {j}", "[0,0,0,0,0,0,0,0]",
                )

        # (a) Routing: every row landed in some partition; totals reconcile.
        total = await pool.fetchval("SELECT count(*) FROM kchunks")
        assert total == len(tenants) * 5
        per_part = await pool.fetch(
            "SELECT tableoid::regclass::text AS part, count(*) c FROM kchunks GROUP BY 1"
        )
        assert len(per_part) >= 2  # rows spread across multiple partitions
        assert sum(r["c"] for r in per_part) == total

        # (b) Pruning: a single-tenant query plans to ONE partition, not all four.
        plan = "\n".join(
            r["QUERY PLAN"]
            for r in await pool.fetch(
                "EXPLAIN SELECT * FROM kchunks WHERE tenant_id = 'tenant-1'"
            )
        )
        import re

        scanned = set(re.findall(r"kchunks_p(\d+)", plan))  # distinct partitions in the plan
        assert len(scanned) == 1, f"expected 1 partition scanned, plan scanned {scanned}:\n{plan}"

        # (c) RLS isolation on the partitioned table under a NON-superuser role.
        await pool.execute("DROP ROLE IF EXISTS part_tenant")
        await pool.execute("CREATE ROLE part_tenant NOSUPERUSER NOBYPASSRLS")
        await pool.execute("GRANT SELECT ON kchunks TO part_tenant")
        async with pool.acquire() as conn:
            await conn.execute("SET ROLE part_tenant")
            await conn.execute("SET app.tenant_id = 'tenant-1'")
            rows = await conn.fetch("SELECT DISTINCT tenant_id FROM kchunks")
            await conn.execute("RESET ROLE")
        seen = {r["tenant_id"] for r in rows}
        assert seen == {"tenant-1"}, f"RLS leak across partitions: {seen}"

        await pool.close()
