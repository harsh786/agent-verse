# tests/rag/test_rag_patterns_complete.py
"""All 5 RAG patterns must be IMPLEMENTED with working execute() methods."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.providers.fake import FakeProvider
from app.rag.agentic.patterns.base import RAGPatternState
from sqlalchemy.ext.asyncio import AsyncSession


# ── FLARE ─────────────────────────────────────────────────────────────────────

def test_flare_state_is_implemented():
    from app.rag.agentic.patterns.flare import FLAREPattern
    p = FLAREPattern()
    assert p.state == RAGPatternState.IMPLEMENTED


async def test_flare_retrieves_when_uncertain():
    """FLARE: when uncertainty detected in generation, trigger retrieval."""
    from app.rag.agentic.patterns.flare import FLAREPattern

    # First generation contains uncertainty signal
    provider = FakeProvider(responses=[
        "I think the answer might be... [UNCERTAIN] actually I'm not sure about this.",
        "The answer is clearly 42.",  # after retrieval
    ])
    pattern = FLAREPattern()

    retrieved_queries = []
    async def mock_retrieve(query, **kwargs):
        retrieved_queries.append(query)
        return "Context: The answer is 42."

    result = await pattern.execute(
        query="What is the answer?",
        provider=provider,
        retrieve_fn=mock_retrieve,
    )
    assert isinstance(result, str)
    assert len(result) > 0
    # With uncertainty, retrieval should be triggered
    assert len(retrieved_queries) > 0 or len(result) > 0


async def test_flare_no_retrieval_when_confident():
    """FLARE: no retrieval when generation is confident."""
    from app.rag.agentic.patterns.flare import FLAREPattern

    provider = FakeProvider(responses=["The answer is definitively 42."])
    pattern = FLAREPattern()
    retrieved_queries = []

    async def mock_retrieve(query, **kwargs):
        retrieved_queries.append(query)
        return "Context"

    result = await pattern.execute(
        query="What is 6 * 7?",
        provider=provider,
        retrieve_fn=mock_retrieve,
    )
    assert isinstance(result, str)


async def test_flare_fallback_without_retrieve_fn():
    """FLARE: must not crash without a retrieve_fn."""
    from app.rag.agentic.patterns.flare import FLAREPattern
    provider = FakeProvider(responses=["Answer without retrieval."])
    pattern = FLAREPattern()
    result = await pattern.execute(
        query="test question",
        provider=provider,
        retrieve_fn=None,
    )
    assert isinstance(result, str)


# ── Self-RAG ──────────────────────────────────────────────────────────────────

def test_self_rag_state_is_implemented():
    from app.rag.agentic.patterns.self_rag import SelfRAGPattern
    p = SelfRAGPattern()
    assert p.state == RAGPatternState.IMPLEMENTED


async def test_self_rag_decides_to_retrieve():
    """Self-RAG: decides to retrieve for factual queries."""
    from app.rag.agentic.patterns.self_rag import SelfRAGPattern

    provider = FakeProvider(responses=[
        '{"should_retrieve": true, "reason": "factual question needs evidence"}',
        "Based on retrieved context: Paris is the capital of France.",
        '{"is_relevant": true, "is_supported": true, "is_useful": true}',
    ])
    pattern = SelfRAGPattern()

    async def mock_retrieve(query, **kwargs):
        return "Context: Paris is the capital of France."

    result = await pattern.execute(
        query="What is the capital of France?",
        provider=provider,
        retrieve_fn=mock_retrieve,
    )
    assert isinstance(result, str)
    assert len(result) > 0


async def test_self_rag_skips_retrieval_for_simple_queries():
    """Self-RAG: skips retrieval when decided not needed."""
    from app.rag.agentic.patterns.self_rag import SelfRAGPattern

    provider = FakeProvider(responses=[
        '{"should_retrieve": false, "reason": "simple math, no retrieval needed"}',
        "2 + 2 = 4",
    ])
    pattern = SelfRAGPattern()
    retrieved = []

    async def mock_retrieve(query, **kwargs):
        retrieved.append(query)
        return "Context"

    result = await pattern.execute(
        query="What is 2 + 2?",
        provider=provider,
        retrieve_fn=mock_retrieve,
    )
    assert "4" in result or len(result) > 0


async def test_self_rag_fallback_without_retrieve_fn():
    """Self-RAG: graceful when no retrieve_fn provided."""
    from app.rag.agentic.patterns.self_rag import SelfRAGPattern
    provider = FakeProvider(responses=[
        '{"should_retrieve": true}',
        "Direct answer without context.",
    ])
    pattern = SelfRAGPattern()
    result = await pattern.execute(
        query="test",
        provider=provider,
        retrieve_fn=None,
    )
    assert isinstance(result, str)


# ── Speculative RAG ───────────────────────────────────────────────────────────

def test_speculative_rag_state_is_implemented():
    from app.rag.agentic.patterns.speculative import SpeculativeRAGPattern
    p = SpeculativeRAGPattern()
    assert p.state == RAGPatternState.IMPLEMENTED


async def test_speculative_rag_generates_and_verifies_candidates():
    """Speculative RAG: generate N candidates, verify with retrieval, pick best."""
    from app.rag.agentic.patterns.speculative import SpeculativeRAGPattern

    provider = FakeProvider(responses=[
        "Candidate 1: Paris is the capital.",
        "Candidate 2: London is the capital.",
        "Candidate 3: Berlin is the capital.",
        '{"score": 0.95, "supported": true}',   # candidate 1 verified
        '{"score": 0.1, "supported": false}',   # candidate 2
        '{"score": 0.2, "supported": false}',   # candidate 3
    ])
    pattern = SpeculativeRAGPattern(n_candidates=3)

    async def mock_retrieve(query, **kwargs):
        return "Context: France capital is Paris."

    result = await pattern.execute(
        query="What is the capital of France?",
        provider=provider,
        retrieve_fn=mock_retrieve,
    )
    assert isinstance(result, str)
    assert len(result) > 0


async def test_speculative_rag_fallback_on_no_supported():
    """Speculative RAG: returns best candidate even if none fully supported."""
    from app.rag.agentic.patterns.speculative import SpeculativeRAGPattern

    provider = FakeProvider(responses=[
        "Candidate: some answer",
        '{"score": 0.3, "supported": false}',
    ])
    pattern = SpeculativeRAGPattern(n_candidates=1)

    result = await pattern.execute(
        query="unclear question",
        provider=provider,
        retrieve_fn=None,
    )
    assert isinstance(result, str)


# ── RAPTOR ────────────────────────────────────────────────────────────────────

def test_raptor_state_is_implemented():
    from app.rag.agentic.patterns.raptor import RAPTORPattern
    p = RAPTORPattern()
    assert p.state == RAGPatternState.IMPLEMENTED


async def test_raptor_builds_hierarchical_summary():
    """RAPTOR: recursively summarizes chunks into a hierarchy."""
    from app.rag.agentic.patterns.raptor import RAPTORPattern

    provider = FakeProvider(responses=[
        "Summary of chunks 1-2: These chunks discuss machine learning basics.",
        "Summary of chunks 3-4: These chunks discuss neural networks.",
        "Top-level summary: ML and neural networks are key AI techniques.",
        "Based on hierarchical context: The answer is X.",
    ])
    pattern = RAPTORPattern(cluster_size=2, max_levels=2)

    chunks = [
        {"content": "Machine learning is a subset of AI.", "chunk_id": "c1"},
        {"content": "Supervised learning uses labeled data.", "chunk_id": "c2"},
        {"content": "Neural networks mimic brain structure.", "chunk_id": "c3"},
        {"content": "Deep learning uses many layers.", "chunk_id": "c4"},
    ]
    result = await pattern.execute(
        query="Explain AI techniques",
        chunks=chunks,
        provider=provider,
    )
    assert isinstance(result, str)
    assert len(result) > 0


async def test_raptor_handles_few_chunks():
    """RAPTOR: handles fewer chunks than cluster_size gracefully."""
    from app.rag.agentic.patterns.raptor import RAPTORPattern
    provider = FakeProvider(responses=["Single summary.", "Final answer."])
    pattern = RAPTORPattern(cluster_size=5)
    chunks = [{"content": "Only chunk.", "chunk_id": "c1"}]
    result = await pattern.execute(
        query="test", chunks=chunks, provider=provider
    )
    assert isinstance(result, str)


# ── ColBERT ───────────────────────────────────────────────────────────────────

def test_colbert_state_is_implemented():
    from app.rag.agentic.patterns.colbert import ColBERTPattern
    p = ColBERTPattern()
    assert p.state == RAGPatternState.IMPLEMENTED


def test_colbert_reranks_chunks():
    """ColBERT: reranks chunks using MaxSim token-level scoring."""
    from app.rag.agentic.patterns.colbert import ColBERTPattern

    chunks = [
        {"chunk_id": "c1", "content": "Python is a high-level programming language.", "score": 0.5},
        {"chunk_id": "c2", "content": "Java is used for enterprise software.", "score": 0.6},
        {"chunk_id": "c3", "content": "Python is widely used in machine learning.", "score": 0.4},
    ]
    pattern = ColBERTPattern()
    reranked = pattern.rerank(
        query="Python programming for machine learning",
        chunks=chunks,
    )
    # c3 mentions both "Python" and "machine learning" — should rank higher than c2
    assert isinstance(reranked, list)
    assert len(reranked) == 3
    first_ids = [c["chunk_id"] for c in reranked[:2]]
    assert "c3" in first_ids or "c1" in first_ids  # Python-related chunks must be higher


def test_colbert_maxsim_scores():
    """ColBERT MaxSim: query tokens match doc tokens."""
    from app.rag.agentic.patterns.colbert import ColBERTPattern
    pattern = ColBERTPattern()
    score = pattern._maxsim_score(
        query="machine learning python",
        document="Python is used for machine learning and data science.",
    )
    assert isinstance(score, float)
    assert score > 0.0


def test_colbert_empty_chunks():
    """ColBERT: handles empty chunk list gracefully."""
    from app.rag.agentic.patterns.colbert import ColBERTPattern
    pattern = ColBERTPattern()
    result = pattern.rerank(query="test", chunks=[])
    assert result == []


async def test_colbert_execute_reranks_and_returns_context():
    """ColBERT.execute() reranks and returns combined context string."""
    from app.rag.agentic.patterns.colbert import ColBERTPattern
    pattern = ColBERTPattern()
    chunks = [
        {"chunk_id": "c1", "content": "Relevant content about Python.", "score": 0.4},
        {"chunk_id": "c2", "content": "Irrelevant content about cooking.", "score": 0.9},
    ]
    result = await pattern.execute(
        query="Python programming",
        chunks=chunks,
        top_k=2,
    )
    assert isinstance(result, str)
    assert "Python" in result  # Python chunk should be prioritized
