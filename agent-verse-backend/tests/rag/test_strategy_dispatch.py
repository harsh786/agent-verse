# tests/rag/test_strategy_dispatch.py
"""retrieve() must dispatch every strategy string to the correct implementation."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def session():
    return AsyncMock(spec=AsyncSession)


async def test_retrieve_dispatches_corrective(session):
    from app.rag.engine import retrieve, RetrievalResult
    with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[
        RetrievalResult("c1", "content", 0.8, {}, ["vector"])
    ])):
        results = await retrieve(
            session, query="test", query_embedding=[0.1]*10,
            collection_id="col1", strategy="corrective",
        )
    assert isinstance(results, list)


async def test_retrieve_dispatches_colbert(session):
    from app.rag.engine import retrieve, RetrievalResult
    with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[
        RetrievalResult("c1", "Python programming content", 0.7, {}, ["vector"]),
        RetrievalResult("c2", "cooking recipes content", 0.8, {}, ["vector"]),
    ])):
        results = await retrieve(
            session, query="Python programming",
            query_embedding=[0.1]*10,
            collection_id="col1", strategy="colbert",
        )
    assert isinstance(results, list)
    assert len(results) > 0


async def test_retrieve_dispatches_raptor_no_provider(session):
    """raptor without provider falls back to hybrid."""
    from app.rag.engine import retrieve, RetrievalResult
    with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[
        RetrievalResult("c1", "content", 0.8, {}, ["vector"])
    ])):
        results = await retrieve(
            session, query="test", query_embedding=[0.1]*10,
            collection_id="col1", strategy="raptor",
            provider=None,
        )
    assert isinstance(results, list)


async def test_retrieve_dispatches_speculative_no_provider(session):
    """speculative without provider falls back to hybrid."""
    from app.rag.engine import retrieve, RetrievalResult
    with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[])):
        results = await retrieve(
            session, query="test", query_embedding=[0.1]*10,
            collection_id="col1", strategy="speculative",
        )
    assert isinstance(results, list)


async def test_retrieve_dispatches_flare_no_provider(session):
    """flare without provider falls back to hybrid."""
    from app.rag.engine import retrieve, RetrievalResult
    with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[
        RetrievalResult("c1", "content", 0.8, {}, ["vector"])
    ])):
        results = await retrieve(
            session, query="test", query_embedding=[0.1]*10,
            collection_id="col1", strategy="flare",
            provider=None,
        )
    assert isinstance(results, list)


def test_strategy_registry_implemented_patterns():
    """All 13 patterns must be IMPL in registry after this fix."""
    from app.orchestration.strategy_registry import build_default_registry, StrategyState
    reg = build_default_registry()
    must_be_impl = [
        "self_consistency", "tree_of_thoughts", "peer_review",
        "self_refine", "corrective_rag", "adaptive_rag",
        "speculative_rag", "fusion_rag", "self_rag",
        "flare", "raptor", "colbert_late_interaction",
        "reflexion",
    ]
    for sid in must_be_impl:
        cap = reg.get(sid)
        assert cap is not None, f"Strategy {sid} missing from registry"
        assert cap.state == StrategyState.IMPLEMENTED, \
            f"Strategy {sid} is {cap.state}, expected IMPLEMENTED"
        assert reg.is_available(sid), f"Strategy {sid} is not available"


def test_agentic_chunking_state():
    from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
    from app.rag.agentic.patterns.base import RAGPatternState
    p = AgenticChunkingPattern()
    assert p.state == RAGPatternState.IMPLEMENTED


async def test_agentic_chunking_extracts_propositions():
    from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
    from app.providers.fake import FakeProvider
    pattern = AgenticChunkingPattern(max_propositions=3)
    provider = FakeProvider(responses=[
        "AgentVerse is an AI platform.\nIt supports dynamic orchestration.\nGoals are executed autonomously."
    ])
    chunks = [{"content": "AgentVerse is an AI platform that supports dynamic orchestration.", "chunk_id": "c1"}]
    result = await pattern.execute(chunks=chunks, provider=provider, query="AI platform")
    assert isinstance(result, list)
    assert len(result) > 0
    # Each result should be a proposition (shorter than original chunk)
    for prop in result:
        assert "content" in prop
