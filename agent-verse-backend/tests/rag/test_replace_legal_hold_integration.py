"""P1d-5: a document under legal hold is never replaced (real Postgres).

Re-ingesting a URL (and a re-upload, and a connector re-sync) replaces the
document's previous version: its chunks are deleted in the writing
transaction. ``POST /knowledge/ingest/url`` did that without any hold check.
The store now refuses the replacement inside that transaction when the
document, its collection or the tenant is held — whatever the caller checked.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/rag/test_replace_legal_hold_integration.py -m integration
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


def _version(doc: str, text: str) -> list[Chunk]:
    return [
        Chunk(
            document_id=doc,
            content=f"{text} part {i}",
            embedding=[0.01 * (i + 1)] * 768,
            chunk_index=i,
            metadata={"doc_content_hash": f"{doc}-{text}"},
        )
        for i in range(2)
    ]


async def _contents(pg_url: str, cid: str, doc: str) -> list[str]:
    rows = await admin_exec(
        pg_url,
        "SELECT content FROM knowledge_chunks_768 WHERE collection_id = :c "
        "AND document_id = :d ORDER BY chunk_index",
        {"c": cid, "d": doc},
    )
    return [str(r[0]) for r in rows]


async def _hold(pg_url: str, tid: str, resource_type: str, *ids: str) -> None:
    await admin_exec(
        pg_url,
        "INSERT INTO legal_holds (tenant_id, name, resource_type, resource_ids) "
        "VALUES (:t, 'matter', :rt, CAST(:r AS jsonb))",
        {"t": tid, "rt": resource_type, "r": json.dumps(list(ids))},
    )


async def _seed(pg_url: str) -> tuple[KnowledgeStore, TenantContext, str, object]:
    tid = str(uuid.uuid4())
    await seed_tenant(pg_url, tid)
    engine = await app_engine(pg_url)
    store = KnowledgeStore(sessions(engine), embedding_dim=768)
    ctx = TenantContext(tid, PlanTier.FREE, "k")
    cid = await store.create_collection_async(KnowledgeCollection(name="web"), tenant_ctx=ctx)
    await store.ingest_chunks_async(
        _version("url-doc", "v1"), collection_id=cid, tenant_ctx=ctx, replace_document=True
    )
    return store, ctx, cid, engine


@pytest.mark.parametrize("scope", ["document", "collection", "tenant"])
async def test_a_held_document_is_not_replaced(pg_url: str, scope: str) -> None:
    store, ctx, cid, engine = await _seed(pg_url)
    try:
        if scope == "document":
            await _hold(pg_url, ctx.tenant_id, "document", "url-doc")
        elif scope == "collection":
            await _hold(pg_url, ctx.tenant_id, "collection", cid)
        else:
            await _hold(pg_url, ctx.tenant_id, "tenant")
        with pytest.raises(KnowledgeLegalHoldError):
            await store.ingest_chunks_async(
                _version("url-doc", "v2"), collection_id=cid, tenant_ctx=ctx,
                replace_document=True,
            )
        assert await _contents(pg_url, cid, "url-doc") == ["v1 part 0", "v1 part 1"]
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


async def test_released_or_unrelated_holds_do_not_block(pg_url: str) -> None:
    store, ctx, cid, engine = await _seed(pg_url)
    try:
        await _hold(pg_url, ctx.tenant_id, "document", "another-doc")
        await _hold(pg_url, ctx.tenant_id, "document", "url-doc")
        await admin_exec(
            pg_url,
            "UPDATE legal_holds SET status = 'released' WHERE tenant_id = :t "
            "AND resource_ids @> CAST(:r AS jsonb)",
            {"t": ctx.tenant_id, "r": json.dumps(["url-doc"])},
        )
        await store.ingest_chunks_async(
            _version("url-doc", "v2"), collection_id=cid, tenant_ctx=ctx, replace_document=True
        )
        assert await _contents(pg_url, cid, "url-doc") == ["v2 part 0", "v2 part 1"]
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


async def test_a_tenant_hold_does_not_block_a_new_document(pg_url: str) -> None:
    """Nothing is deleted when the document is new: a hold has nothing to protect."""
    store, ctx, cid, engine = await _seed(pg_url)
    try:
        await _hold(pg_url, ctx.tenant_id, "tenant")
        await store.ingest_chunks_async(
            _version("new-doc", "v1"), collection_id=cid, tenant_ctx=ctx, replace_document=True
        )
        assert await _contents(pg_url, cid, "new-doc") == ["v1 part 0", "v1 part 1"]
    finally:
        await engine.dispose()  # type: ignore[attr-defined]
