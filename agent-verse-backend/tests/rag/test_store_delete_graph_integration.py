"""KB-04: deleting a document or collection also deletes its knowledge-graph rows,
and a collection's chunks are removed in bounded batches.

``knowledge_nodes`` / ``knowledge_edges`` have no FK to collections, so entities
extracted from deleted documents kept appearing in GraphRAG; and collection
delete removed every chunk in one cascading transaction.

Real Postgres (minimal schema) — run with::

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/rag/test_store_delete_graph_integration.py -q -m integration
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.rag import store as store_mod
from app.rag.store import SUPPORTED_EMBEDDING_DIMENSIONS, KnowledgeStore, _chunk_table
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration

TENANT = "t-kb04"
CTX = TenantContext(tenant_id=TENANT, plan=PlanTier.PROFESSIONAL, api_key_id="k")
TABLE = _chunk_table(768)

_SCHEMA = [
    """CREATE TABLE legal_holds (
        id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
        tenant_id TEXT NOT NULL, name TEXT NOT NULL, resource_type TEXT NOT NULL,
        resource_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
        status TEXT NOT NULL DEFAULT 'active', expires_at TIMESTAMPTZ)""",
    """CREATE TABLE knowledge_collections (
        id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, embedding_dim INT NOT NULL,
        is_active BOOLEAN DEFAULT TRUE, chunk_count INT DEFAULT 0,
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
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
            collection_id TEXT NOT NULL REFERENCES knowledge_collections(id) ON DELETE CASCADE,
            document_id TEXT NOT NULL, content TEXT, chunk_index INT DEFAULT 0)"""
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
        tables = [_chunk_table(d) for d in SUPPORTED_EMBEDDING_DIMENSIONS]
        for table in [
            *tables,
            "knowledge_collections",
            "knowledge_nodes",
            "knowledge_edges",
            "knowledge_node_mentions",
            "legal_holds",
        ]:
            await conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        for ddl in _SCHEMA:
            await conn.execute(text(ddl))
        await conn.execute(
            text(
                "INSERT INTO knowledge_collections (id, tenant_id, embedding_dim, chunk_count, "
                "document_count) VALUES ('col', :t, 768, 5, 2), ('other', :t, 768, 1, 1)"
            ),
            {"t": TENANT},
        )
        rows = [
            ("c-a0", "col", "doc-a", 0),
            ("c-a1", "col", "doc-a", 1),
            ("c-b0", "col", "doc-b", 0),
            ("c-b1", "col", "doc-b", 1),
            ("c-b2", "col", "doc-b", 2),
            ("c-o0", "other", "doc-o", 0),
        ]
        for cid, col, doc, idx in rows:
            await conn.execute(
                text(
                    f"INSERT INTO {TABLE} (id, tenant_id, collection_id, document_id, content, "
                    "chunk_index) VALUES (:id, :t, :c, :d, 'x', :i)"
                ),
                {"id": cid, "t": TENANT, "c": col, "d": doc, "i": idx},
            )
        # Nodes stamped by the ingestion hook: chunk ids (current) and
        # "<doc>:<idx>" (legacy), plus an edge between two of doc-a's nodes.
        nodes = [
            ("n-a0", "c-a0"),
            ("n-a1", "doc-a:1"),
            ("n-b0", "c-b0"),
            ("n-b2", "doc-b:2"),
            ("n-o0", "c-o0"),
        ]
        for nid, src in nodes:
            await conn.execute(
                text("INSERT INTO knowledge_nodes (id, tenant_id, source_id) VALUES (:n, :t, :s)"),
                {"n": nid, "t": TENANT, "s": src},
            )
        await conn.execute(
            text(
                "INSERT INTO knowledge_edges VALUES "
                "('e1', :t, 'n-a0', 'n-a1'), ('e2', :t, 'n-b0', 'n-o0')"
            ),
            {"t": TENANT},
        )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _ids(db: async_sessionmaker, table: str) -> set[str]:  # type: ignore[type-arg]
    async with db() as s:
        return {r[0] for r in (await s.execute(text(f"SELECT id FROM {table}"))).fetchall()}


async def test_document_delete_removes_its_graph_rows(db: async_sessionmaker) -> None:  # type: ignore[type-arg]
    store = KnowledgeStore(db)

    deleted = await store.delete_document_async("doc-a", collection_id="col", tenant_ctx=CTX)

    assert deleted == 2
    assert await _ids(db, "knowledge_nodes") == {"n-b0", "n-b2", "n-o0"}
    assert await _ids(db, "knowledge_edges") == {"e2"}


async def test_collection_delete_is_batched_and_removes_graph_rows(
    db: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    store = KnowledgeStore(db)
    statements: list[str] = []
    real_text = text

    def _spy(sql: str) -> object:
        statements.append(sql)
        return real_text(sql)

    with (
        patch.object(store_mod, "_COLLECTION_DELETE_BATCH", 2),
        patch("sqlalchemy.text", _spy),
    ):
        assert await store.delete_collection_async("col", tenant_ctx=CTX) is True

    assert await _ids(db, TABLE) == {"c-o0"}
    assert await _ids(db, "knowledge_collections") == {"other"}
    # Only the other collection's entity (and no edge touching a deleted node) is left.
    assert await _ids(db, "knowledge_nodes") == {"n-o0"}
    assert await _ids(db, "knowledge_edges") == set()
    # 5 chunks at 2 per batch: several bounded chunk DELETEs, not one cascade.
    chunk_deletes = [s for s in statements if s.startswith(f"DELETE FROM {TABLE}")]
    assert len(chunk_deletes) >= 3
