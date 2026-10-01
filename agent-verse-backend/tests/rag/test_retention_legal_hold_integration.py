"""KB-03: knowledge chunk expiry skips chunks under an in-force legal hold.

The TTL sweep deleted expired chunks, documents and graph nodes of tenants under
a tenant-wide hold (and of held collections / documents). Real Postgres, since
the exemption is SQL (legal_holds JSONB ``resource_ids`` containment).

Run with::

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/rag/test_retention_legal_hold_integration.py -q -m integration
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.rag.retention import expire_knowledge_chunks
from app.rag.store import SUPPORTED_EMBEDDING_DIMENSIONS, _chunk_table

pytestmark = pytest.mark.integration

_SCHEMA = [
    """CREATE TABLE legal_holds (
        id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
        tenant_id TEXT NOT NULL, name TEXT NOT NULL, resource_type TEXT NOT NULL,
        resource_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
        status TEXT NOT NULL DEFAULT 'active', expires_at TIMESTAMPTZ)""",
    """CREATE TABLE knowledge_collections (
        id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, chunk_count INT DEFAULT 0,
        document_count INT DEFAULT 0, total_size_bytes BIGINT DEFAULT 0,
        updated_at TIMESTAMPTZ)""",
    """CREATE TABLE knowledge_nodes (id TEXT PRIMARY KEY, tenant_id TEXT, source_id TEXT)""",
    """CREATE TABLE knowledge_edges (
        id TEXT PRIMARY KEY, tenant_id TEXT, source_node_id TEXT, target_node_id TEXT,
        provenance TEXT)""",
    """CREATE TABLE knowledge_node_mentions (
        tenant_id TEXT NOT NULL, node_id TEXT NOT NULL, chunk_id TEXT NOT NULL,
        document_id TEXT, PRIMARY KEY (tenant_id, node_id, chunk_id))""",
    *[
        f"""CREATE TABLE {_chunk_table(dim)} (
            id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text, tenant_id TEXT NOT NULL,
            collection_id TEXT NOT NULL, document_id TEXT NOT NULL, content TEXT,
            chunk_index INT DEFAULT 0, expires_at TIMESTAMPTZ)"""
        for dim in SUPPORTED_EMBEDDING_DIMENSIONS
    ],
]


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest_asyncio.fixture
async def db(postgres_url: str) -> AsyncIterator[async_sessionmaker]:  # type: ignore[type-arg]
    engine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        for table in (
            "legal_holds",
            "knowledge_collections",
            "knowledge_nodes",
            "knowledge_edges",
            "knowledge_node_mentions",
            *[_chunk_table(d) for d in SUPPORTED_EMBEDDING_DIMENSIONS],
        ):
            await conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
        for ddl in _SCHEMA:
            await conn.execute(text(ddl))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed_expired(db: async_sessionmaker, tenant: str, collection: str, doc: str) -> None:  # type: ignore[type-arg]
    table = _chunk_table(768)
    async with db() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO knowledge_collections (id, tenant_id, chunk_count, document_count) "
                "VALUES (:c, :t, 1, 1) ON CONFLICT DO NOTHING"
            ),
            {"c": collection, "t": tenant},
        )
        await s.execute(
            text(
                f"INSERT INTO {table} (tenant_id, collection_id, document_id, content, expires_at) "
                "VALUES (:t, :c, :d, 'x', now() - interval '1 day')"
            ),
            {"t": tenant, "c": collection, "d": doc},
        )


async def _hold(db: async_sessionmaker, tenant: str, rtype: str, ids: str = "[]") -> None:  # type: ignore[type-arg]
    async with db() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO legal_holds (tenant_id, name, resource_type, resource_ids) "
                "VALUES (:t, 'matter', :r, CAST(:ids AS jsonb))"
            ),
            {"t": tenant, "r": rtype, "ids": ids},
        )


async def _remaining(db: async_sessionmaker) -> set[tuple[str, str]]:  # type: ignore[type-arg]
    async with db() as s:
        rows = await s.execute(text(f"SELECT tenant_id, document_id FROM {_chunk_table(768)}"))
        return {(r[0], r[1]) for r in rows.fetchall()}


async def test_expiry_skips_held_tenants_collections_and_documents(
    db: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    await _seed_expired(db, "t-free", "c-free", "d-free")
    await _seed_expired(db, "t-tenant-hold", "c-a", "d-a")
    await _seed_expired(db, "t-res", "c-held", "d-in-held-collection")
    await _seed_expired(db, "t-res", "c-open", "d-held")
    await _seed_expired(db, "t-res", "c-open", "d-open")
    await _hold(db, "t-tenant-hold", "tenant")
    await _hold(db, "t-res", "collection", '["c-held", "d-held"]')

    totals = await expire_knowledge_chunks(system_db=db, app_db=db)

    assert await _remaining(db) == {
        ("t-tenant-hold", "d-a"),
        ("t-res", "d-in-held-collection"),
        ("t-res", "d-held"),
    }
    assert totals["knowledge_chunks_expired"] == 2


async def test_released_or_expired_hold_no_longer_protects(
    db: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    await _seed_expired(db, "t-old", "c", "d")
    await _hold(db, "t-old", "tenant")
    async with db() as s, s.begin():
        await s.execute(text("UPDATE legal_holds SET status = 'released'"))

    await expire_knowledge_chunks(system_db=db, app_db=db)

    assert await _remaining(db) == set()
