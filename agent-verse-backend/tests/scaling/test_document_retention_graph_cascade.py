"""Integration test: expiring a document must expire the graph extracted from it.

Two defects this covers, both in `expire_stale_documents`:

1. **The retention DELETE never ran.** `documents` is ENABLE + FORCE ROW LEVEL
   SECURITY, and the task opened a plain session with no RLS context — so under
   any role subject to RLS it matched zero rows while reporting
   `{"status": "ok", "deleted": 0}`. Sibling maintenance tasks in the same
   module already use `system_session` (see tests/scaling/test_maintenance_rls_fix.py).

2. **The knowledge graph outlived its sources.** `knowledge_nodes.source_id`
   holds the `documents.id` a node was extracted from, but the ONLY delete path
   for the KG tables was `KnowledgeGraphStore.delete_tenant_graph` — a
   whole-tenant wipe. Expiring a document therefore left its extracted entities
   and relations behind permanently: unbounded growth, and Graph RAG continuing
   to serve evidence derived from content the retention policy had deleted.

This is deliberately NOT age-expiry of curated knowledge. It deletes exactly the
graph rows whose source document the EXISTING retention policy is deleting, and
leaves everything else — including graph rows with no source document — alone.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/scaling/test_document_retention_graph_cascade.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TENANT = "tenant-retention"
RETENTION_DAYS = 90


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": admin_url},
            check=True,
            capture_output=True,
            text=True,
        )
        yield admin_url


@pytest_asyncio.fixture(scope="function")
async def seeded(postgres_url: str) -> AsyncIterator[async_sessionmaker]:
    """One expired document and one fresh one, each with its own graph rows.

    The maintenance connection is a BYPASSRLS-capable role, which is what
    `system_session` requires (see its docstring) and how a maintenance worker
    must be provisioned.
    """
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    coll = secrets.token_hex(8)
    async with factory() as s:
        for tbl in ("knowledge_edges", "knowledge_nodes", "documents",
                    "knowledge_collections"):
            await s.execute(
                text(f"DELETE FROM {tbl} WHERE tenant_id = :t"), {"t": TENANT}
            )
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, email) VALUES (:t, 'T', :e) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"t": TENANT, "e": f"{TENANT}@example.test"},
        )
        await s.execute(
            text(
                "INSERT INTO knowledge_collections (id, tenant_id, name) "
                "VALUES (:c, :t, :n) ON CONFLICT (id) DO NOTHING"
            ),
            {"c": coll, "t": TENANT, "n": f"c-{coll[:8]}"},
        )
        for doc_id, age_days in (("doc-old", 200), ("doc-new", 1)):
            await s.execute(
                text(
                    "INSERT INTO documents "
                    "(id, collection_id, tenant_id, source, content, content_hash, "
                    " chunk_index, created_at) "
                    "VALUES (:id, :c, :t, 'upload', 'body', :h, 0, "
                    "        NOW() - make_interval(days => :age))"
                ),
                {"id": doc_id, "c": coll, "t": TENANT, "h": doc_id, "age": age_days},
            )
            # Graph extracted from that document: source_id == documents.id
            for suffix in ("a", "b"):
                await s.execute(
                    text(
                        "INSERT INTO knowledge_nodes "
                        "(id, tenant_id, node_type, label, content, source_id, "
                        " confidence, created_at, updated_at) "
                        "VALUES (:id, :t, 'entity', :l, 'c', :src, 0.9, NOW(), NOW())"
                    ),
                    {
                        "id": f"{doc_id}-n{suffix}",
                        "t": TENANT,
                        "l": f"{doc_id}-{suffix}",
                        "src": doc_id,
                    },
                )
            await s.execute(
                text(
                    "INSERT INTO knowledge_edges "
                    "(id, tenant_id, source_node_id, target_node_id, edge_type, "
                    " confidence, created_at) "
                    "VALUES (:id, :t, :s, :g, 'rel', 0.9, NOW())"
                ),
                {
                    "id": f"{doc_id}-e",
                    "t": TENANT,
                    "s": f"{doc_id}-na",
                    "g": f"{doc_id}-nb",
                },
            )
        # A graph node with no source document at all must be left alone.
        await s.execute(
            text(
                "INSERT INTO knowledge_nodes "
                "(id, tenant_id, node_type, label, content, source_id, confidence, "
                " created_at, updated_at) "
                "VALUES ('manual-node', :t, 'concept', 'curated', 'c', NULL, 1.0, "
                "        NOW() - make_interval(days => 400), NOW())"
            ),
            {"t": TENANT},
        )
        await s.commit()
    yield factory
    await engine.dispose()


async def _ids(factory: async_sessionmaker, sql: str) -> set[str]:
    async with factory() as s:
        rows = (await s.execute(text(sql), {"t": TENANT})).fetchall()
    return {r[0] for r in rows}


@pytest.mark.asyncio
async def test_expiring_a_document_also_expires_its_graph(
    seeded: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db import session as db_session
    from app.scaling.tasks import _expire_stale_documents

    monkeypatch.setattr(db_session, "get_session_factory", lambda: seeded)

    result = await _expire_stale_documents(RETENTION_DAYS)
    assert result["status"] == "ok", result
    assert result["deleted"] == 1, result
    assert result["graph_nodes_deleted"] == 2, result
    assert result["graph_edges_deleted"] == 1, result

    docs = await _ids(factory := seeded, "SELECT id FROM documents WHERE tenant_id = :t")
    assert docs == {"doc-new"}, docs

    nodes = await _ids(factory, "SELECT id FROM knowledge_nodes WHERE tenant_id = :t")
    # The expired document's graph is gone; the fresh document's and the
    # source-less curated node survive.
    assert "doc-old-na" not in nodes and "doc-old-nb" not in nodes, nodes
    assert {"doc-new-na", "doc-new-nb", "manual-node"} <= nodes, nodes

    edges = await _ids(factory, "SELECT id FROM knowledge_edges WHERE tenant_id = :t")
    assert edges == {"doc-new-e"}, edges


@pytest.mark.asyncio
async def test_retention_is_a_no_op_when_nothing_is_expired(
    seeded: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long window must delete nothing at all — including no graph rows."""
    from app.db import session as db_session
    from app.scaling.tasks import _expire_stale_documents

    monkeypatch.setattr(db_session, "get_session_factory", lambda: seeded)

    result = await _expire_stale_documents(3650)
    assert result == {
        "status": "ok",
        "deleted": 0,
        "graph_nodes_deleted": 0,
        "graph_edges_deleted": 0,
    }, result

    nodes = await _ids(seeded, "SELECT id FROM knowledge_nodes WHERE tenant_id = :t")
    assert len(nodes) == 5, nodes
