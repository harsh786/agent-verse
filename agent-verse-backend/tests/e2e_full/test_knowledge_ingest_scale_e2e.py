"""e2e_full: ingestion must stay linear as a collection grows to millions of chunks.

Two defects made bulk ingestion quadratic, both invisible at the handful-of-docs
scale every other test ingests at:

1. **Counter recompute per document.** Every ``_persist_chunks`` call finished
   with ``UPDATE knowledge_collections SET chunk_count = (SELECT count(*) ...),
   document_count = (SELECT count(DISTINCT document_id) ...), total_size_bytes =
   (SELECT sum(octet_length(content)) ...)`` — three full aggregate scans of the
   collection's entire chunk table, the last of which detoasts every chunk's
   text. Ingesting N documents therefore cost O(N²) work; at a million documents
   (~10M chunks) a single further ingest reads 30M rows.

2. **Unindexed dedup probe.** ``exists_by_hash`` — called on *every* document —
   filters on ``content_hash = :h OR metadata->>'doc_content_hash' = :h``, and
   neither ``content_hash`` nor that JSONB expression had any index, so the probe
   degraded into a scan of the tenant's chunks.

These tests assert the *shape* of the work (plans and statements), not wall-clock
timing, so they stay meaningful on a small fixture and cannot flake on a loaded
CI box.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_STORE_SRC = Path(__file__).resolve().parents[2] / "app" / "rag" / "store.py"


def _counter_update_statements() -> list[str]:
    """Return every ``UPDATE knowledge_collections`` statement in the store."""
    src = _STORE_SRC.read_text()
    return [
        src[m.start() : src.index("WHERE id = :", m.start())]
        for m in re.finditer(r"UPDATE knowledge_collections\s*\n\s*SET chunk_count", src)
    ]


@pytest.mark.asyncio(loop_scope="session")
async def test_collection_counters_are_incremental_not_recomputed() -> None:
    """No counter update may aggregate over the whole chunk table.

    Source-level because the cost is structural: a correlated
    ``SELECT count(*) FROM knowledge_chunks_<dim>`` inside the per-document
    UPDATE is O(collection) work on every single ingest, and no amount of
    fixture data makes that visible as a behavioural assertion the way it is
    visible here.
    """
    statements = _counter_update_statements()
    assert statements, "no knowledge_collections counter update found — test is stale"
    for stmt in statements:
        assert "count(*)" not in stmt, (
            f"counter update recomputes chunk_count by scanning the chunk table:\n{stmt}"
        )
        assert "count(DISTINCT" not in stmt, (
            f"counter update recomputes document_count by scanning the chunk table:\n{stmt}"
        )
        assert "octet_length" not in stmt or "sum(" not in stmt, (
            f"counter update re-sums every chunk's content (detoasting the table):\n{stmt}"
        )


async def _make_collection(tenant_client: Any) -> str:
    resp = await tenant_client.post(
        "/knowledge/collections",
        json={"name": f"scale-{uuid.uuid4().hex[:8]}", "description": "scale audit"},
    )
    assert resp.status_code in (200, 201), resp.text
    return str(resp.json()["collection_id"])


async def test_dedup_probe_uses_an_index_not_a_scan(app: Any, tenant_client: Any) -> None:
    """The per-document dedup probe must be index-driven.

    ``exists_by_hash`` runs once per ingested document. With no index on
    ``content_hash`` and none on ``metadata->>'doc_content_hash'``, Postgres can
    only filter, so the probe's cost grows with the collection — the single
    hottest query in a bulk load.
    """
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    me = (await tenant_client.get("/tenants/me")).json()
    tenant_id = str(me["tenant_id"])
    collection_id = await _make_collection(tenant_client)

    factory = app.state.db_session_factory
    async with (
        factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        dim = (
            await session.execute(
                text(
                    "SELECT embedding_dim FROM knowledge_collections "
                    "WHERE id = :cid AND tenant_id = :tid"
                ),
                {"cid": collection_id, "tid": tenant_id},
            )
        ).scalar_one()
        table = f"knowledge_chunks_{int(dim)}"

        # Assert index *coverage* directly. An EXPLAIN on a near-empty fixture
        # table only reports what the planner happens to cost out there, and
        # forcing enable_seqscan=off would make any index at all satisfy the
        # assertion — including the HNSW/trigram ones that cannot answer this
        # predicate. Both arms of the OR need their own B-tree for the planner
        # to BitmapOr them at real volume.
        index_defs = [
            str(r[0])
            for r in (
                await session.execute(
                    text("SELECT indexdef FROM pg_indexes WHERE tablename = :t"),
                    {"t": table},
                )
            ).fetchall()
        ]

    def _covers(column_expr: str) -> bool:
        return any(
            column_expr in d and "btree" in d and "tenant_id" in d for d in index_defs
        )

    assert _covers("content_hash"), (
        f"{table} has no B-tree index covering content_hash — the per-document "
        f"dedup probe scans the collection. Indexes present:\n" + "\n".join(index_defs)
    )
    assert _covers("doc_content_hash"), (
        f"{table} has no B-tree index covering metadata->>'doc_content_hash' — "
        f"the dedup probe and the ingest TOCTOU guard both scan the collection. "
        f"Indexes present:\n" + "\n".join(index_defs)
    )


async def test_counters_stay_exact_across_ingest_and_delete(
    app: Any, tenant_client: Any
) -> None:
    """Incremental counters must agree with the ground truth in the chunk table."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context
    from app.rag.models import Chunk
    from app.tenancy.context import PlanTier, TenantContext

    me = (await tenant_client.get("/tenants/me")).json()
    tenant_id = str(me["tenant_id"])
    collection_id = await _make_collection(tenant_client)
    ctx = TenantContext(tenant_id=tenant_id, api_key_id="scale", plan=PlanTier.FREE)

    store = app.state.knowledge_store
    factory = app.state.db_session_factory

    async def _truth() -> tuple[int, int, int]:
        async with (
            factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            dim = (
                await session.execute(
                    text(
                        "SELECT embedding_dim FROM knowledge_collections "
                        "WHERE id = :cid AND tenant_id = :tid"
                    ),
                    {"cid": collection_id, "tid": tenant_id},
                )
            ).scalar_one()
            table = f"knowledge_chunks_{int(dim)}"
            row = (
                await session.execute(
                    text(
                        f"SELECT count(*), count(DISTINCT document_id), "
                        f"COALESCE(sum(octet_length(content)), 0) FROM {table} "
                        "WHERE collection_id = :cid AND tenant_id = :tid"
                    ),
                    {"cid": collection_id, "tid": tenant_id},
                )
            ).fetchone()
            return int(row[0]), int(row[1]), int(row[2])

    async def _stored() -> tuple[int, int, int]:
        async with (
            factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            row = (
                await session.execute(
                    text(
                        "SELECT chunk_count, document_count, total_size_bytes "
                        "FROM knowledge_collections WHERE id = :cid AND tenant_id = :tid"
                    ),
                    {"cid": collection_id, "tid": tenant_id},
                )
            ).fetchone()
            return int(row[0]), int(row[1]), int(row[2] or 0)

    dim = await store.get_collection_embedding_dim(collection_id, tenant_ctx=ctx)
    dim = int(dim or 1536)

    def _chunks(doc_id: str, n: int, body: str) -> list[Chunk]:
        return [
            Chunk(
                chunk_id=uuid.uuid4().hex,
                document_id=doc_id,
                content=f"{body} — part {i} — ünïcödé",
                embedding=[0.01 * ((i + j) % 7) for j in range(dim)],
                metadata={"doc_content_hash": f"{doc_id}-hash"},
                chunk_index=i,
            )
            for i in range(n)
        ]

    doc_a, doc_b = str(uuid.uuid4()), str(uuid.uuid4())
    await store.ingest_chunks_async(
        _chunks(doc_a, 3, "alpha"), collection_id=collection_id, tenant_ctx=ctx
    )
    assert await _stored() == await _truth()

    await store.ingest_chunks_async(
        _chunks(doc_b, 5, "beta"), collection_id=collection_id, tenant_ctx=ctx
    )
    stored, truth = await _stored(), await _truth()
    assert stored == truth, f"counters drifted after a second document: {stored} != {truth}"
    assert stored[1] == 2, f"document_count should be 2, got {stored[1]}"

    deleted = await store.delete_document_async(
        doc_a, collection_id=collection_id, tenant_ctx=ctx
    )
    assert deleted == 3
    stored, truth = await _stored(), await _truth()
    assert stored == truth, f"counters drifted after a delete: {stored} != {truth}"
    assert stored[1] == 1, f"document_count should be 1 after deleting one doc, got {stored[1]}"


async def test_db_backed_store_does_not_mirror_chunks_in_process(
    app: Any, tenant_client: Any
) -> None:
    """A persisted store must not keep every ingested chunk on the Python heap.

    ``ingest_chunks_async`` used to ``cached.chunks.extend(chunks)`` even in DB
    mode, and then recompute ``document_count`` with
    ``len({c.document_id for c in cached.chunks})`` — an unbounded per-replica
    memory leak plus O(n) work per ingest. At a million documents the API and
    every Celery worker hold the whole corpus in RAM. The DB path never reads
    that mirror back (searches and counters both go to Postgres).
    """
    import uuid as _uuid

    from app.rag.models import Chunk
    from app.tenancy.context import PlanTier, TenantContext

    me = (await tenant_client.get("/tenants/me")).json()
    tenant_id = str(me["tenant_id"])
    collection_id = await _make_collection(tenant_client)
    ctx = TenantContext(tenant_id=tenant_id, api_key_id="scale", plan=PlanTier.FREE)

    store = app.state.knowledge_store
    assert store._db is not None, "this test is about the DB-backed path"
    dim = int(await store.get_collection_embedding_dim(collection_id, tenant_ctx=ctx) or 1536)

    for n in range(6):
        await store.ingest_chunks_async(
            [
                Chunk(
                    chunk_id=_uuid.uuid4().hex,
                    document_id=str(_uuid.uuid4()),
                    content=f"corpus document {n}",
                    embedding=[0.02] * dim,
                    metadata={"doc_content_hash": f"mirror-{n}"},
                    chunk_index=0,
                )
            ],
            collection_id=collection_id,
            tenant_ctx=ctx,
        )

    cached = store._data.get((tenant_id, collection_id))
    mirrored = len(cached.chunks) if cached is not None else 0
    assert mirrored == 0, (
        f"DB-backed store mirrored {mirrored} chunks onto the heap; at millions of "
        "documents this is an unbounded per-replica memory leak"
    )
