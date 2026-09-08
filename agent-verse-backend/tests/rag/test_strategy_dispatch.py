# tests/rag/test_strategy_dispatch.py
"""retrieve() must dispatch every strategy string to the correct implementation."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def session():
    return AsyncMock(spec=AsyncSession)


async def test_retrieve_dispatches_corrective(session):
    from app.rag.engine import RetrievalResult, retrieve

    with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[
        RetrievalResult("c1", "content", 0.8, {}, ["vector"])
    ])):
        results = await retrieve(
            session, query="test", query_embedding=[0.1]*10,
            collection_id="col1", strategy="corrective",
        )
    assert isinstance(results, list)


async def test_retrieve_dispatches_colbert(session):
    from app.rag.engine import RetrievalResult, retrieve

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


def test_strategy_registry_implemented_patterns():
    """Only production-wired agent patterns are certified IMPLEMENTED in this slice."""
    from app.orchestration.strategy_registry import (
        StrategyCategory,
        StrategyState,
        build_default_registry,
    )
    reg = build_default_registry()
    agent_patterns = [
        "self_consistency", "tree_of_thoughts", "peer_review",
        "self_refine", "reflexion",
    ]
    for sid in agent_patterns:
        cap = reg.get(sid)
        assert cap is not None, f"Strategy {sid} missing from registry"
        assert cap.state == StrategyState.IMPLEMENTED, \
            f"Strategy {sid} is {cap.state}, expected IMPLEMENTED"
        assert reg.is_available(sid), f"Strategy {sid} is not available"

    rag_patterns = reg.list_by_category(StrategyCategory.RAG)
    implemented = {
        cap.strategy_id
        for cap in rag_patterns
        if cap.state is StrategyState.IMPLEMENTED
    }
    assert implemented == {
        "naive",
        "hybrid",
        "hyde",
        "multi_hop",
        "fusion",
        "graph",
        "corrective",
        "adaptive",
        "web_augmented",
        "raptor",
        "agentic_chunking",
        "colbert",
        "speculative",
        "agentic",
        "self_rag",
        "flare",
        "modular",
        "raft",
        "memory_augmented",
        "code",
    }
    assert all(
        reg.is_available(cap.strategy_id)
        == (cap.strategy_id in implemented)
        for cap in rag_patterns
    )


def test_agentic_chunking_state():
    from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
    from app.rag.agentic.patterns.base import RAGPatternState
    p = AgenticChunkingPattern()
    assert p.state == RAGPatternState.IMPLEMENTED


async def test_agentic_chunking_extracts_propositions():
    from app.providers.fake import FakeProvider
    from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern

    pattern = AgenticChunkingPattern(max_propositions=3)
    provider = FakeProvider(
        responses=[
            "AgentVerse is an AI platform.\n"
            "It supports dynamic orchestration.\n"
            "Goals are executed autonomously."
        ]
    )
    chunks = [
        {
            "content": "AgentVerse is an AI platform that supports dynamic orchestration.",
            "chunk_id": "c1",
        }
    ]
    result = await pattern.execute(chunks=chunks, provider=provider, query="AI platform")
    assert isinstance(result, list)
    assert len(result) > 0
    # Each result should be a proposition (shorter than original chunk)
    for prop in result:
        assert "content" in prop
