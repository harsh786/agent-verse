"""metadata_filter must be accessible through RetrieverTool.retrieve()."""
from __future__ import annotations
import pytest
from unittest.mock import patch, AsyncMock
from app.rag.agentic.retriever_tool import RetrieverTool, RetrievalResult
from app.rag.store import KnowledgeStore
from app.rag.models import KnowledgeCollection, Chunk
from app.tenancy.context import TenantContext, PlanTier


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def store_with_metadata(tenant_ctx):
    store = KnowledgeStore()
    col = KnowledgeCollection(name="docs", collection_id="col1", embedder="fake")
    store.create_collection(col, tenant_ctx=tenant_ctx)
    store.ingest_chunk(
        Chunk(document_id="d1", content="Alice doc", embedding=[0.9]*10,
              chunk_index=0, chunk_id="c1",
              metadata={"author": "alice", "source_url": "https://a.com"}),
        collection_id="col1", tenant_ctx=tenant_ctx,
    )
    store.ingest_chunk(
        Chunk(document_id="d2", content="Bob doc", embedding=[0.5]*10,
              chunk_index=0, chunk_id="c2",
              metadata={"author": "bob", "source_url": "https://b.com"}),
        collection_id="col1", tenant_ctx=tenant_ctx,
    )
    return store


def test_retriever_tool_retrieve_accepts_metadata_filter():
    """retrieve() must accept metadata_filter kwarg."""
    import inspect
    sig = inspect.signature(RetrieverTool.retrieve)
    assert "metadata_filter" in sig.parameters


async def test_retriever_tool_filters_by_metadata(store_with_metadata, tenant_ctx):
    """retrieve() with metadata_filter must only return matching chunks."""
    tool = RetrieverTool(knowledge_store=store_with_metadata)
    result = await tool.retrieve(
        query="doc",
        tenant_ctx=tenant_ctx,
        collection_ids=["col1"],
        metadata_filter={"author": "alice"},
        min_confidence=0.0,
    )
    # Only alice's chunk should be in result
    chunk_ids = {c["chunk_id"] for c in result.chunks}
    assert "c1" in chunk_ids, "Alice's chunk must be present"
    assert "c2" not in chunk_ids, "Bob's chunk must be filtered out"


async def test_retriever_tool_no_filter_returns_all(store_with_metadata, tenant_ctx):
    """retrieve() without metadata_filter must return all chunks."""
    tool = RetrieverTool(knowledge_store=store_with_metadata)
    result = await tool.retrieve(
        query="doc",
        tenant_ctx=tenant_ctx,
        collection_ids=["col1"],
        min_confidence=0.0,
    )
    chunk_ids = {c["chunk_id"] for c in result.chunks}
    assert "c1" in chunk_ids
    assert "c2" in chunk_ids
