# tests/rag/test_adaptive_rag.py
"""Adaptive RAG: pattern selects strategy based on query heuristics."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession


def test_adaptive_rag_pattern_state_implemented():
    from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern
    from app.rag.agentic.patterns.base import RAGPatternState
    assert AdaptiveRAGPattern().state == RAGPatternState.IMPLEMENTED


def test_adaptive_rag_selects_lexical_for_ticket_ids():
    from app.rag.engine import RetrievalPlanner
    planner = RetrievalPlanner()
    assert planner.select_strategy("Find ticket JIRA-123") == "lexical"


def test_adaptive_rag_selects_hyde_for_abstract_queries():
    from app.rag.engine import RetrievalPlanner
    planner = RetrievalPlanner()
    assert planner.select_strategy("what is dynamic orchestration") == "hyde"


def test_adaptive_rag_selects_multi_hop_for_comparison():
    from app.rag.engine import RetrievalPlanner
    planner = RetrievalPlanner()
    assert planner.select_strategy("compare agents across all tenants") == "multi_hop"


def test_adaptive_rag_selects_direct_for_general():
    from app.rag.engine import RetrievalPlanner
    planner = RetrievalPlanner()
    assert planner.select_strategy("list all active agents") == "direct"


async def test_adaptive_rag_pattern_execute_dispatches_correctly():
    """AdaptiveRAGPattern.execute() must call retrieve() with auto strategy."""
    from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern

    pattern = AdaptiveRAGPattern()
    mock_session = AsyncMock(spec=AsyncSession)
    called_strategy = []

    async def fake_retrieve(session, *, strategy=None, **kwargs):
        called_strategy.append(strategy)
        return []

    with patch("app.rag.agentic.patterns.adaptive.retrieve", side_effect=fake_retrieve):
        await pattern.execute(
            session=mock_session,
            query="what is AgentVerse",
            query_embedding=[0.1] * 10,
            collection_id="col1",
        )

    assert len(called_strategy) == 1
    assert called_strategy[0] == "hyde"


def test_adaptive_rag_pattern_has_execute():
    from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern
    assert hasattr(AdaptiveRAGPattern, "execute")
