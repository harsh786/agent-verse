# tests/rag/test_corrective_rag.py
"""Corrective RAG (CRAG): score retrieval → correct via fallback if low quality."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.rag.agentic.retriever_tool import RetrieverTool, RetrievalResult
from app.rag.store import KnowledgeStore
from app.tenancy.context import TenantContext, PlanTier


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


async def test_crag_accepts_high_confidence_result(tenant_ctx):
    """When KB returns high confidence, CRAG returns it without fallback."""
    tool = RetrieverTool(knowledge_store=KnowledgeStore())

    high_conf = RetrievalResult(
        query="test", source="knowledge_base",
        strategy_used="hybrid", confidence=0.85,
        context_text="High quality content about AgentVerse.",
        chunks=[{"content": "AgentVerse dynamic orchestration", "score": 0.85}],
    )

    with patch.object(tool, "_retrieve_from_kb", AsyncMock(return_value=high_conf)):
        result = await tool.retrieve_corrective(
            query="what is AgentVerse",
            tenant_ctx=tenant_ctx,
            confidence_threshold=0.5,
        )

    assert result.source == "knowledge_base"
    assert result.confidence >= 0.85
    assert result.corrected is False


async def test_crag_triggers_web_fallback_on_low_confidence(tenant_ctx):
    """When KB returns low confidence, CRAG falls back to web search."""
    web_calls = []

    async def mock_web(query, top_k=3):
        web_calls.append(query)
        return [{"content": f"Web: {query}", "url": "https://web.example.com"}]

    low_conf = RetrievalResult(
        query="test", source="knowledge_base",
        strategy_used="hybrid", confidence=0.15,
        context_text="Insufficient information found.",
        chunks=[],
    )

    tool = RetrieverTool(
        knowledge_store=KnowledgeStore(),
        web_search_fn=mock_web,
        web_search_available=True,
    )

    with patch.object(tool, "_retrieve_from_kb", AsyncMock(return_value=low_conf)):
        result = await tool.retrieve_corrective(
            query="obscure topic nobody knows",
            tenant_ctx=tenant_ctx,
            confidence_threshold=0.5,
        )

    assert len(web_calls) > 0, "Web search should have been called"
    assert result.corrected is True
    assert result.correction_reason == "low_confidence"


async def test_crag_triggers_fallback_on_gap_signals(tenant_ctx):
    """When KB response contains gap signals, CRAG triggers correction."""
    web_calls = []

    async def mock_web(query, top_k=3):
        web_calls.append(query)
        return [{"content": "Corrected result", "url": "https://web.example.com"}]

    gap_result = RetrievalResult(
        query="test", source="knowledge_base",
        strategy_used="hybrid", confidence=0.65,
        context_text="Cannot determine the answer from available information.",
        chunks=[{"content": "Cannot determine the answer.", "score": 0.65}],
    )

    tool = RetrieverTool(
        knowledge_store=KnowledgeStore(),
        web_search_fn=mock_web,
        web_search_available=True,
    )

    with patch.object(tool, "_retrieve_from_kb", AsyncMock(return_value=gap_result)):
        result = await tool.retrieve_corrective(
            query="what is the meaning of life",
            tenant_ctx=tenant_ctx,
            confidence_threshold=0.5,
        )

    assert len(web_calls) > 0, "Web search should be triggered by gap signal"
    assert result.corrected is True
    assert result.correction_reason == "gap_detected"


async def test_crag_returns_best_when_web_also_fails(tenant_ctx):
    """When both KB and web fail, CRAG returns parametric fallback gracefully."""
    low_conf = RetrievalResult(
        query="test", source="knowledge_base",
        strategy_used="hybrid", confidence=0.1,
        context_text="No information found.",
        chunks=[],
    )

    tool = RetrieverTool(knowledge_store=KnowledgeStore())  # no web

    with patch.object(tool, "_retrieve_from_kb", AsyncMock(return_value=low_conf)):
        result = await tool.retrieve_corrective(
            query="impossible query",
            tenant_ctx=tenant_ctx,
            confidence_threshold=0.5,
        )

    assert result is not None
    assert result.source in ("knowledge_base", "parametric", "none_available")


def test_corrective_rag_pattern_state_implemented():
    from app.rag.agentic.patterns.corrective import CorrectiveRAGPattern
    from app.rag.agentic.patterns.base import RAGPatternState
    assert CorrectiveRAGPattern().state == RAGPatternState.IMPLEMENTED


def test_corrective_pattern_has_execute():
    from app.rag.agentic.patterns.corrective import CorrectiveRAGPattern
    assert hasattr(CorrectiveRAGPattern, "execute")
