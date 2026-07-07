"""RetrieverTool fires all available sources in parallel via asyncio.gather."""
from __future__ import annotations

import asyncio
import time
import pytest
from app.rag.agentic.retriever_tool import RetrieverTool, RetrievalResult
from app.rag.store import KnowledgeStore
from app.rag.models import KnowledgeCollection, Chunk
from app.tenancy.context import TenantContext, PlanTier


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def loaded_store(tenant_ctx):
    store = KnowledgeStore()
    col = KnowledgeCollection(name="docs", collection_id="col1", embedder="fake")
    store.create_collection(col, tenant_ctx=tenant_ctx)
    store.ingest_chunk(
        Chunk(document_id="d1", content="Orchestration content about agents", embedding=[0.1]*10,
              chunk_index=0, chunk_id="c1", metadata={}),
        collection_id="col1", tenant_ctx=tenant_ctx,
    )
    return store


async def test_parallel_retrieve_returns_result(tenant_ctx, loaded_store):
    tool = RetrieverTool(knowledge_store=loaded_store, web_search_available=False)
    results = await tool.parallel_retrieve(
        query="orchestration", tenant_ctx=tenant_ctx, sources=["kb"], top_k=3,
    )
    assert isinstance(results, list)
    assert len(results) >= 1
    assert all(hasattr(r, "source") for r in results)


async def test_parallel_retrieve_no_source_available(tenant_ctx):
    tool = RetrieverTool()
    results = await tool.parallel_retrieve(
        query="any query", tenant_ctx=tenant_ctx, sources=["kb"], top_k=3,
    )
    assert isinstance(results, list)
    # May return empty or parametric — never raises


async def test_parallel_retrieve_multiple_sources(tenant_ctx, loaded_store):
    async def fake_web_search(query, top_k=3):
        return [{"content": "web result", "url": "https://web.example.com"}]

    tool = RetrieverTool(knowledge_store=loaded_store, web_search_fn=fake_web_search, web_search_available=True)
    results = await tool.parallel_retrieve(
        query="test", tenant_ctx=tenant_ctx, sources=["kb", "web"], top_k=3,
    )
    assert len(results) >= 1


async def test_parallel_retrieve_never_returns_empty_on_all_failures(tenant_ctx):
    """parallel_retrieve must never return empty list — even when all sources fail."""
    tool = RetrieverTool(knowledge_store=KnowledgeStore())
    results = await tool.parallel_retrieve(
        query="any query", tenant_ctx=tenant_ctx, sources=["kb"],
    )
    assert isinstance(results, list)
    assert len(results) >= 1  # MUST never be empty
    assert all(isinstance(r, RetrievalResult) for r in results)
