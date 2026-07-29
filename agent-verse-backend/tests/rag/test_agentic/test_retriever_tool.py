# tests/rag/test_agentic/test_retriever_tool.py
"""RetrieverTool must NEVER return silent empty strings — always a structured result."""
from __future__ import annotations

import pytest

from app.rag.agentic.retriever_tool import RetrievalResult, RetrieverTool
from app.rag.agentic.source_inventory import SourceInventory, SourceInventoryResult
from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def empty_store():
    return KnowledgeStore()


@pytest.fixture
def loaded_store(tenant_ctx):
    store = KnowledgeStore()
    col = KnowledgeCollection(name="docs", collection_id="col1", embedder="fake")
    store.create_collection(col, tenant_ctx=tenant_ctx)
    chunk = Chunk(
        document_id="d1",
        content="The AgentVerse platform supports dynamic orchestration.",
        embedding=[0.1] * 10,
        chunk_index=0,
        chunk_id="c1",
        metadata={"source_url": "https://docs.example.com/page1", "page_number": 1},
    )
    store.ingest_chunk(chunk, collection_id="col1", tenant_ctx=tenant_ctx)
    return store


# ── Never return empty strings ────────────────────────────────────────────────

async def test_empty_kb_returns_structured_result_not_empty_string(tenant_ctx, empty_store):
    tool = RetrieverTool(knowledge_store=empty_store)
    result = await tool.retrieve(
        query="what is agentverse",
        tenant_ctx=tenant_ctx,
    )
    assert isinstance(result, RetrievalResult)
    # Must NOT be silent empty — must have a source explanation
    assert result.source in ("none_available", "parametric", "web", "memory")
    assert result.confidence >= 0.0


async def test_retrieval_returns_content_when_kb_has_data(tenant_ctx, loaded_store):
    tool = RetrieverTool(knowledge_store=loaded_store)
    result = await tool.retrieve(
        query="dynamic orchestration",
        tenant_ctx=tenant_ctx,
        collection_ids=["col1"],
    )
    assert isinstance(result, RetrievalResult)
    assert len(result.chunks) > 0
    assert result.confidence > 0.0
    assert result.source == "knowledge_base"


async def test_retrieval_result_has_citations(tenant_ctx, loaded_store):
    tool = RetrieverTool(knowledge_store=loaded_store)
    result = await tool.retrieve(
        query="platform",
        tenant_ctx=tenant_ctx,
        collection_ids=["col1"],
    )
    # Citations must be structured, not empty
    assert isinstance(result.citations, list)


async def test_retrieval_respects_min_confidence(tenant_ctx, loaded_store):
    tool = RetrieverTool(knowledge_store=loaded_store)
    result = await tool.retrieve(
        query="completely unrelated topic xyz123",
        tenant_ctx=tenant_ctx,
        collection_ids=["col1"],
        min_confidence=0.9,   # very high threshold
    )
    # Low-confidence results filtered out → graceful degradation
    assert isinstance(result, RetrievalResult)


# ── SourceInventory ───────────────────────────────────────────────────────────

async def test_source_inventory_empty_kb(tenant_ctx, empty_store):
    inventory = SourceInventory(knowledge_store=empty_store)
    result = await inventory.build(tenant_ctx=tenant_ctx)
    assert isinstance(result, SourceInventoryResult)
    assert result.kb_collections == 0
    assert result.kb_state == "empty"


async def test_source_inventory_with_data(tenant_ctx, loaded_store):
    inventory = SourceInventory(knowledge_store=loaded_store)
    result = await inventory.build(tenant_ctx=tenant_ctx)
    assert result.kb_collections >= 1
    assert result.kb_state in ("sparse", "healthy")


async def test_source_inventory_knows_web_available(tenant_ctx, empty_store):
    inventory = SourceInventory(knowledge_store=empty_store, web_search_available=True)
    result = await inventory.build(tenant_ctx=tenant_ctx)
    assert result.web_available is True


# ── Strategy routing ─────────────────────────────────────────────────────────

async def test_strategy_auto_selects_web_when_kb_empty(tenant_ctx, empty_store):
    tool = RetrieverTool(knowledge_store=empty_store, web_search_available=True)
    result = await tool.retrieve(
        query="current Python version",
        tenant_ctx=tenant_ctx,
        strategy="auto",
    )
    # With empty KB, auto should route to web or parametric
    assert result.source in ("web", "parametric", "none_available")
    assert result.strategy_used is not None


# ── Extra coverage ────────────────────────────────────────────────────────────

async def test_source_inventory_to_dict(tenant_ctx, empty_store):
    inventory = SourceInventory(knowledge_store=empty_store, web_search_available=True)
    result = await inventory.build(tenant_ctx=tenant_ctx)
    d = result.to_dict()
    assert isinstance(d, dict)
    assert "kb_collections" in d
    assert "web_available" in d
    assert d["web_available"] is True


async def test_retrieval_context_text_property(tenant_ctx, loaded_store):
    tool = RetrieverTool(knowledge_store=loaded_store)
    result = await tool.retrieve(
        query="dynamic orchestration",
        tenant_ctx=tenant_ctx,
        collection_ids=["col1"],
    )
    # context_text must be a non-empty string when chunks present
    assert isinstance(result.context_text, str)
    if result.chunks:
        assert len(result.context_text) > 0


async def test_parallel_retrieve_never_returns_empty_on_all_failures(tenant_ctx):
    """parallel_retrieve must never return empty list — even when all sources fail."""
    # Use empty store with no web search — will fail all attempts
    tool = RetrieverTool(knowledge_store=KnowledgeStore())
    results = await tool.parallel_retrieve(
        query="any query",
        tenant_ctx=tenant_ctx,
        sources=["kb"],
    )
    assert isinstance(results, list)
    assert len(results) >= 1  # MUST never be empty
    assert all(isinstance(r, RetrievalResult) for r in results)
