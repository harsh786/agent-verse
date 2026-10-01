"""KB-33: deleting a collection honours DOCUMENT-level legal holds (real Postgres).

The collection delete checked holds only on the collection id, so a hold on one
document was bypassed by deleting its collection.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/rag/test_collection_delete_legal_hold_integration.py -m integration
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeLegalHoldError, KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from tests.rag._pg import admin_exec, app_engine, seed_tenant, sessions

pytestmark = pytest.mark.integration


def _chunks(doc: str, n: int = 3) -> list[Chunk]:
    return [
        Chunk(
            document_id=doc,
            content=f"{doc} part {i}",
            embedding=[0.01 * (i + 1)] * 768,
            chunk_index=i,
        )
        for i in range(n)
    ]


async def _seed(pg_url: str) -> tuple[KnowledgeStore, TenantContext, str, object]:
    tid = str(uuid.uuid4())
    await seed_tenant(pg_url, tid)
    engine = await app_engine(pg_url)
    store = KnowledgeStore(sessions(engine), embedding_dim=768)
    ctx = TenantContext(tid, PlanTier.FREE, "k")
    cid = await store.create_collection_async(KnowledgeCollection(name="c"), tenant_ctx=ctx)
    for doc in ("doc-a", "doc-held"):
        await store.ingest_chunks_async(_chunks(doc), collection_id=cid, tenant_ctx=ctx)
    return store, ctx, cid, engine


async def _hold(pg_url: str, tid: str, *resource_ids: str) -> None:
    await admin_exec(
        pg_url,
        "INSERT INTO legal_holds (tenant_id, name, resource_type, resource_ids) "
        "VALUES (:t, 'matter', 'document', CAST(:r AS jsonb))",
        {"t": tid, "r": json.dumps(list(resource_ids))},
    )


async def _chunk_count(pg_url: str, cid: str) -> int:
    rows = await admin_exec(
        pg_url, "SELECT count(*) FROM knowledge_chunks_768 WHERE collection_id = :c", {"c": cid}
    )
    return int(rows[0][0])


async def test_document_hold_blocks_collection_delete(pg_url: str) -> None:
    store, ctx, cid, engine = await _seed(pg_url)
    try:
        await _hold(pg_url, ctx.tenant_id, "doc-held")
        assert await store.collection_under_legal_hold_async(cid, tenant_ctx=ctx) is True
        with pytest.raises(KnowledgeLegalHoldError):
            await store.delete_collection_async(cid, tenant_ctx=ctx)
        assert await _chunk_count(pg_url, cid) == 6
        assert await store.get_collection_async(cid, tenant_ctx=ctx) is not None
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


async def test_hold_on_another_collections_document_does_not_block(pg_url: str) -> None:
    store, ctx, cid, engine = await _seed(pg_url)
    try:
        await _hold(pg_url, ctx.tenant_id, "doc-elsewhere", "not-this-collection")
        assert await store.collection_under_legal_hold_async(cid, tenant_ctx=ctx) is False
        assert await store.delete_collection_async(cid, tenant_ctx=ctx) is True
        assert await _chunk_count(pg_url, cid) == 0
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


async def test_collection_and_tenant_holds_are_seen(pg_url: str) -> None:
    store, ctx, cid, engine = await _seed(pg_url)
    try:
        await _hold(pg_url, ctx.tenant_id, cid)
        assert await store.collection_under_legal_hold_async(cid, tenant_ctx=ctx) is True
        await admin_exec(
            pg_url, "DELETE FROM legal_holds WHERE tenant_id = :t", {"t": ctx.tenant_id}
        )
        await admin_exec(
            pg_url,
            "INSERT INTO legal_holds (tenant_id, name, resource_type) VALUES (:t, 'm', 'tenant')",
            {"t": ctx.tenant_id},
        )
        assert await store.collection_under_legal_hold_async(cid, tenant_ctx=ctx) is True
        await admin_exec(
            pg_url,
            "UPDATE legal_holds SET status = 'released' WHERE tenant_id = :t",
            {"t": ctx.tenant_id},
        )
        assert await store.collection_under_legal_hold_async(cid, tenant_ctx=ctx) is False
    finally:
        await engine.dispose()  # type: ignore[attr-defined]
