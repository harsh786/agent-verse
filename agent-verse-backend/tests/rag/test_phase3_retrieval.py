# tests/rag/test_phase3_retrieval.py
"""Phase 3: RAG/Retrieval gap fixes."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.engine import RetrievalResult


@pytest.fixture
def session():
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def base_results():
    return [RetrievalResult("c1", "content about python", 0.8, {}, ["vector"])]


async def test_retrieve_parametric_returns_empty(session):
    """parametric strategy skips retrieval entirely."""
    from app.rag.engine import retrieve
    results = await retrieve(
        session, query="test", query_embedding=[0.1]*10,
        collection_id="col1", strategy="parametric",
    )
    assert results == []


async def test_retrieve_memory_returns_empty(session):
    """memory strategy returns empty (LTM handled separately)."""
    from app.rag.engine import retrieve
    results = await retrieve(
        session, query="test", query_embedding=[0.1]*10,
        collection_id="col1", strategy="memory",
    )
    assert results == []


async def test_retrieve_graph_falls_back_to_hybrid(session, base_results):
    """graph strategy falls back to hybrid when no kg_store."""
    from app.rag.engine import retrieve
    with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=base_results)):
        results = await retrieve(
            session, query="test", query_embedding=[0.1]*10,
            collection_id="col1", strategy="graph",
        )
    assert isinstance(results, list)


async def test_retrieve_agentic_chunking_dispatched(session, base_results):
    """agentic_chunking is now in dispatch table."""
    from app.rag.engine import retrieve
    with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=base_results)):
        results = await retrieve(
            session, query="test", query_embedding=[0.1]*10,
            collection_id="col1", strategy="agentic_chunking",
            provider=None,  # no provider → falls back to base results
        )
    assert isinstance(results, list)


async def test_query_expander_llm_path():
    """expand_for_fusion_async uses LLM when provider given."""
    from app.providers.fake import FakeProvider
    from app.rag.agentic.query_expander import QueryExpander
    expander = QueryExpander()
    provider = FakeProvider(responses=["Alternative 1\nAlternative 2\nAlternative 3"])
    variants = await expander.expand_for_fusion_async(
        "python machine learning", max_variants=4, provider=provider
    )
    assert len(variants) >= 2
    assert "python machine learning" in variants


async def test_query_reformulator_llm_path():
    """reformulate_async uses LLM when provider given."""
    from app.providers.fake import FakeProvider
    from app.rag.agentic.query_reformulator import QueryReformulator
    reformulator = QueryReformulator(max_attempts=2)
    provider = FakeProvider(responses=["Rewrite 1\nRewrite 2"])
    rewrites = await reformulator.reformulate_async("what is AgentVerse", provider=provider)
    assert len(rewrites) >= 1


def test_fallback_chain_next_available_skips_graph_without_infra():
    """FallbackChain must skip graph when kg_store not available."""
    from app.rag.agentic.fallback_chain import FallbackChain
    chain = FallbackChain()
    # No infra → should skip graph and go to hyde
    next_s = chain.next_available_strategy("hybrid", available_infra=set())
    assert next_s != "graph"  # graph requires kg_store
    assert next_s in ("hyde", "web", "ltm", "parametric")


def test_fallback_chain_uses_graph_when_infra_available():
    """FallbackChain uses graph when kg_store available."""
    from app.rag.agentic.fallback_chain import FallbackChain
    chain = FallbackChain()
    next_s = chain.next_available_strategy("hybrid", available_infra={"kg_store"})
    assert next_s == "graph"


def test_cross_encoder_no_runtime_error_in_async_context():
    """CROSS_ENCODER must not call asyncio.get_event_loop() in production."""
    from app.context.rerank_policy import RerankPolicy
    policy = RerankPolicy()
    chunks = [
        {"chunk_id": "c1", "content": "Python is great", "score": 0.8},
        {"chunk_id": "c2", "content": "Java is also good", "score": 0.6},
    ]
    # Must not raise RuntimeError or DeprecationWarning
    result = policy._cross_encoder_rerank(chunks, "python")
    assert isinstance(result, list)
    assert len(result) == 2
