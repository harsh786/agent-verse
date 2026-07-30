"""Metadata filtering: pre-filter retrieval by JSONB metadata fields."""
from __future__ import annotations

import pytest

from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def store_with_chunks(tenant_ctx):
    store = KnowledgeStore()
    col = KnowledgeCollection(name="docs", collection_id="col1", embedder="fake")
    store.create_collection(col, tenant_ctx=tenant_ctx)
    store.ingest_chunk(
        Chunk(document_id="d1", content="Alice content", embedding=[0.9] * 10,
              chunk_index=0, chunk_id="c1",
              metadata={"author": "alice", "year": 2024, "source_url": "https://a.com"}),
        collection_id="col1", tenant_ctx=tenant_ctx,
    )
    store.ingest_chunk(
        Chunk(document_id="d2", content="Bob content", embedding=[0.5] * 10,
              chunk_index=0, chunk_id="c2",
              metadata={"author": "bob", "year": 2023, "source_url": "https://b.com"}),
        collection_id="col1", tenant_ctx=tenant_ctx,
    )
    return store


def test_hybrid_search_without_filter_returns_all(store_with_chunks, tenant_ctx):
    results = store_with_chunks.hybrid_search(
        query="content",
        query_embedding=[0.7] * 10,
        collection_id="col1",
        tenant_ctx=tenant_ctx,
        top_k=10,
    )
    chunk_ids = {r.chunk_id for r in results}
    assert "c1" in chunk_ids
    assert "c2" in chunk_ids


def test_hybrid_search_with_metadata_filter_author(store_with_chunks, tenant_ctx):
    results = store_with_chunks.hybrid_search(
        query="content",
        query_embedding=[0.7] * 10,
        collection_id="col1",
        tenant_ctx=tenant_ctx,
        top_k=10,
        metadata_filter={"author": "alice"},
    )
    chunk_ids = [r.chunk_id for r in results]
    assert "c1" in chunk_ids, "Alice's chunk must be returned"
    assert "c2" not in chunk_ids, "Bob's chunk must be excluded"


def test_hybrid_search_with_metadata_filter_year(store_with_chunks, tenant_ctx):
    results = store_with_chunks.hybrid_search(
        query="content",
        query_embedding=[0.7] * 10,
        collection_id="col1",
        tenant_ctx=tenant_ctx,
        top_k=10,
        metadata_filter={"year": 2023},
    )
    chunk_ids = [r.chunk_id for r in results]
    assert "c2" in chunk_ids
    assert "c1" not in chunk_ids


def test_hybrid_search_filter_no_match_returns_empty(store_with_chunks, tenant_ctx):
    results = store_with_chunks.hybrid_search(
        query="content",
        query_embedding=[0.7] * 10,
        collection_id="col1",
        tenant_ctx=tenant_ctx,
        top_k=10,
        metadata_filter={"author": "charlie"},
    )
    assert results == []


def test_hybrid_search_db_accepts_metadata_filter_param():
    import inspect

    from app.rag.store import KnowledgeStore
    sig = inspect.signature(KnowledgeStore.hybrid_search_db)
    assert "metadata_filter" in sig.parameters


async def test_engine_hybrid_search_accepts_metadata_filter():
    import inspect

    from app.rag.engine import hybrid_search
    sig = inspect.signature(hybrid_search)
    assert "metadata_filter" in sig.parameters
