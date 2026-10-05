"""USR-3 on real Postgres: the stored collection row names the embedder actually used.

``knowledge_collections.embedder`` was written from the request's label (default
``"voyage"``) and read back with a ``"voyage"`` fallback. A store bound to a
non-voyage embedder now records that embedder (and its dimension) on create,
and the first write records the model that produced the vectors.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def factory(pg_url: str) -> AsyncIterator[Any]:
    engine = create_async_engine(pg_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _tenant(factory: Any) -> TenantContext:
    tid = uuid.uuid4().hex
    async with factory() as s, s.begin():
        await s.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:id, 'T', :e)"),
            {"id": tid, "e": f"{tid}@example.test"},
        )
    return TenantContext(tenant_id=tid, plan=PlanTier.ENTERPRISE, api_key_id="k")


async def _row(factory: Any, cid: str) -> Any:
    async with factory() as s:
        return (
            await s.execute(
                text("SELECT embedder, embedding_dim FROM knowledge_collections WHERE id = :id"),
                {"id": cid},
            )
        ).one()


async def test_created_collection_stores_the_active_non_voyage_embedder(factory: Any) -> None:
    ctx = await _tenant(factory)
    store = KnowledgeStore(factory, embedding_dim=768, embedder_name="all-mpnet-base-v2")
    cid = await store.create_collection_async(KnowledgeCollection(name="docs"), tenant_ctx=ctx)

    assert tuple(await _row(factory, cid)) == ("all-mpnet-base-v2", 768)
    (listed,) = await store.list_collections_async(tenant_ctx=ctx)
    assert (listed.embedder, listed.embedding_dim) == ("all-mpnet-base-v2", 768)
    fetched = await store.get_collection_async(cid, tenant_ctx=ctx)
    assert fetched is not None and fetched.embedder == "all-mpnet-base-v2"


async def test_first_write_records_the_model_that_produced_the_vectors(factory: Any) -> None:
    ctx = await _tenant(factory)
    # A collection created before the fix, labelled "voyage" by the old default.
    store = KnowledgeStore(factory, embedding_dim=1024, embedder_name="nvidia/nv-embed-1024")
    cid = await store.create_collection_async(
        KnowledgeCollection(name="legacy", embedder="voyage"), tenant_ctx=ctx
    )
    await store.ingest_chunks_async(
        [
            Chunk(
                document_id="d1",
                content="the first indexed chunk of this collection",
                embedding=[0.0] * 1023 + [1.0],
                chunk_index=0,
                metadata={"embedding_model": "nvidia/nv-embed-1024"},
            )
        ],
        collection_id=cid,
        tenant_ctx=ctx,
    )
    assert tuple(await _row(factory, cid)) == ("nvidia/nv-embed-1024", 1024)
    counters = await store.collection_counters_async(tenant_ctx=ctx, collection_id=cid)
    assert (counters[0]["embedder"], counters[0]["embedding_dim"]) == (
        "nvidia/nv-embed-1024",
        1024,
    )
