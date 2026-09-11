"""KnowledgeStore.retrieve — the entrypoint workflow LLM/RAG steps call.

Regression guard: retrieve() did not exist, so workflow RAG silently no-oped
(AttributeError swallowed -> empty context). These cover the in-memory path:
name-or-id resolution, a content-bearing result shape, and honest-empty for an
unknown collection.
"""

from __future__ import annotations

import pytest

from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.asyncio

TID = "tenant-kb"


def _ctx() -> TenantContext:
    return TenantContext(tenant_id=TID, api_key_id="k", plan=PlanTier.FREE)


def _store_with_chunk() -> tuple[KnowledgeStore, str]:
    store = KnowledgeStore()  # in-memory (no db factory)
    ctx = _ctx()
    coll = KnowledgeCollection(name="kb", collection_id="c1")
    store.create_collection(coll, tenant_ctx=ctx)
    store.ingest_chunk(
        Chunk(
            document_id="d1",
            content="Pip the toucan is the AgentVerse mascot.",
            embedding=[0.1] * 8,
            chunk_index=0,
        ),
        collection_id="c1",
        tenant_ctx=ctx,
    )
    return store, "kb"


async def test_retrieve_resolves_collection_by_name_and_returns_content():
    store, name = _store_with_chunk()
    hits = await store.retrieve(query="mascot", collection_name=name, top_k=3, tenant_id=TID)
    assert hits, "expected at least one hit"
    assert "content" in hits[0]
    assert "Pip" in hits[0]["content"]


async def test_retrieve_resolves_collection_by_id():
    store, _ = _store_with_chunk()
    hits = await store.retrieve(query="mascot", collection_name="c1", top_k=3, tenant_id=TID)
    assert hits and "Pip" in hits[0]["content"]


async def test_retrieve_unknown_collection_is_empty_not_error():
    store, _ = _store_with_chunk()
    hits = await store.retrieve(query="x", collection_name="does-not-exist", top_k=3, tenant_id=TID)
    assert hits == []


async def test_retrieve_is_tenant_scoped():
    store, name = _store_with_chunk()
    hits = await store.retrieve(
        query="mascot", collection_name=name, top_k=3, tenant_id="other-tenant"
    )
    assert hits == []
