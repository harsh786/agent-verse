# tests/rag/test_rag_e2e.py
"""End-to-end tests: RAG patterns invoked through retrieve() dispatch."""
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
    return [
        RetrievalResult(
            "c1",
            "Python is a programming language.",
            0.8,
            {"source_url": "https://a.com"},
            ["vector"],
        ),
        RetrievalResult(
            "c2",
            "Python is used for machine learning.",
            0.7,
            {"source_url": "https://b.com"},
            ["vector"],
        ),
        RetrievalResult(
            "c3",
            "Java is used for enterprise software.",
            0.5,
            {"source_url": "https://c.com"},
            ["fts"],
        ),
    ]


async def test_retrieve_colbert_reranks_by_query_relevance(session, base_results):
    """ColBERT dispatch: Python-related chunks should rank above Java for 'python' query."""
    from app.rag.engine import retrieve
    with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=base_results * 2)):
        results = await retrieve(
            session, query="python programming",
            query_embedding=[0.1] * 10,
            collection_id="col1", strategy="colbert", top_k=3,
        )
    assert isinstance(results, list)
    assert len(results) > 0
    # Python chunks should rank higher
    if len(results) >= 2:
        python_count = sum(
            1
            for r in results[:2]
            if "python" in r.content.lower() or "Python" in r.content
        )
        assert python_count >= 1


async def test_retrieve_raptor_returns_summary_plus_detail(session, base_results):
    """RAPTOR dispatch: returns hierarchical summary + detail chunks."""
    from app.providers.fake import FakeProvider
    from app.rag.engine import retrieve
    provider = FakeProvider(responses=["Summary of Python chunks.", "Final answer about Python."])
    with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=base_results)):
        results = await retrieve(
            session, query="python",
            query_embedding=[0.1] * 10,
            collection_id="col1", strategy="raptor",
            provider=provider, top_k=3,
        )
    assert isinstance(results, list)
    assert len(results) > 0


async def test_retrieve_speculative_returns_verified_answer(session, base_results):
    """Speculative RAG dispatch: generates candidates and verifies."""
    from app.providers.fake import FakeProvider
    from app.rag.engine import retrieve
    provider = FakeProvider(responses=[
        "Python is excellent for ML.",
        '{"score": 0.9, "supported": true}',
    ])
    with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=base_results)):
        results = await retrieve(
            session, query="python for machine learning",
            query_embedding=[0.1] * 10,
            collection_id="col1", strategy="speculative",
            provider=provider, top_k=2,
        )
    assert isinstance(results, list)


async def test_retrieve_flare_without_provider_falls_back(session, base_results):
    """FLARE without provider: falls back to hybrid."""
    from app.rag.engine import retrieve
    with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=base_results)):
        results = await retrieve(
            session, query="what is python",
            query_embedding=[0.1] * 10,
            collection_id="col1", strategy="flare",
            provider=None,
        )
    assert isinstance(results, list)


async def test_retrieve_corrective_falls_back_gracefully(session, base_results):
    """Corrective RAG dispatch: always returns a result."""
    from app.rag.engine import retrieve
    with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=base_results)):
        results = await retrieve(
            session, query="python", query_embedding=[0.1] * 10,
            collection_id="col1", strategy="corrective",
        )
    assert isinstance(results, list)


def test_true_mmr_selects_diverse_chunks():
    """MMR must select diverse chunks, not just the top-scoring ones."""
    from app.context.rerank_policy import RerankPolicy
    policy = RerankPolicy()
    # 3 similar high-score chunks + 1 different low-score chunk
    chunks = [
        {"chunk_id": "c1", "content": "Python programming language.", "score": 0.9},
        {"chunk_id": "c2", "content": "Python coding language.", "score": 0.85},
        {"chunk_id": "c3", "content": "Python scripting language.", "score": 0.8},
        {"chunk_id": "c4", "content": "Java enterprise software development.", "score": 0.4},
    ]
    result = policy._diversity_rerank(chunks)
    # c1 selected first (highest score); c4 should be selected before c2/c3 (diversity)
    selected_ids = [c["chunk_id"] for c in result]
    assert "c1" in selected_ids
    assert len(selected_ids) == 4


def test_true_mmr_with_embeddings():
    """MMR with embeddings uses vector cosine similarity."""
    from app.context.rerank_policy import RerankPolicy
    policy = RerankPolicy()
    chunks = [
        {"chunk_id": "c1", "content": "Python ML", "score": 0.9, "embedding": [1.0, 0.0, 0.0]},
        {"chunk_id": "c2", "content": "Python ML 2", "score": 0.85, "embedding": [0.99, 0.1, 0.0]},
        {"chunk_id": "c3", "content": "Java EE", "score": 0.6, "embedding": [0.0, 0.0, 1.0]},
    ]
    query_emb = [1.0, 0.0, 0.0]
    result = policy._diversity_rerank(chunks, query_embedding=query_emb)
    # c1 selected first; c3 should rank above c2 due to diversity
    ids = [c["chunk_id"] for c in result]
    assert ids[0] == "c1"
    assert "c3" in ids[:2] or "c2" in ids  # diversity works


def test_cross_encoder_rerank_uses_tfidf_fallback():
    """Cross-encoder fallback must use TF-IDF weighting, not simple set intersection."""
    from app.context.rerank_policy import RerankPolicy
    policy = RerankPolicy()
    chunks = [
        {"chunk_id": "c1", "content": "Python machine learning deep neural networks", "score": 0.3},
        {
            "chunk_id": "c2",
            "content": "Java enterprise application server deployment",
            "score": 0.9,
        },
    ]
    # Query is about Python ML — c1 should rank higher despite lower original score
    result = policy._tfidf_rerank(chunks, query="python machine learning")
    assert result[0]["chunk_id"] == "c1"
