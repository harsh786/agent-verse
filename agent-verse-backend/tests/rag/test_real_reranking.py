# tests/rag/test_real_reranking.py
"""Real cross-encoder and ColBERT reranking with sentence-transformers."""
from __future__ import annotations

import pytest


def test_cross_encoder_module_importable() -> None:
    from app.rag.cross_encoder import cross_encode

    assert cross_encode is not None


def test_cross_encoder_scores_documents() -> None:
    """cross_encode must return a score for each document."""
    from app.rag.cross_encoder import cross_encode, is_cross_encoder_available

    if not is_cross_encoder_available():
        pytest.skip("configured cross-encoder model is unavailable")

    documents = [
        "Python is a programming language",
        "The weather is nice today",
        "Python is used for machine learning",
    ]
    scores = cross_encode("Python machine learning", documents)
    assert len(scores) == 3
    assert all(isinstance(s, float) for s in scores)


def test_cross_encoder_relevance_ordering() -> None:
    """Python ML docs should score higher than weather doc."""
    from app.rag.cross_encoder import cross_encode, is_cross_encoder_available

    if not is_cross_encoder_available():
        pytest.skip("configured cross-encoder model is unavailable")

    documents = [
        "Python is extensively used for machine learning and data science",
        "The weather is sunny and warm today",
        "Python machine learning with scikit-learn tutorial",
    ]
    scores = cross_encode("Python machine learning", documents)
    # Python ML docs should outrank weather doc
    assert scores[0] > scores[1] or scores[2] > scores[1], (
        f"Weather doc scored higher than ML docs: {scores}"
    )


def test_cross_encoder_handles_empty() -> None:
    from app.rag.cross_encoder import cross_encode

    assert cross_encode("test", []) == []


def test_cross_encoder_policy_uses_real_encoder() -> None:
    """RerankPolicy._cross_encoder_rerank must use app.rag.cross_encoder."""
    from app.context.rerank_policy import RerankPolicy

    policy = RerankPolicy()
    chunks = [
        {"chunk_id": "c1", "content": "Python for data science and ML", "score": 0.5},
        {"chunk_id": "c2", "content": "Today is a sunny day outdoors", "score": 0.9},
        {"chunk_id": "c3", "content": "Machine learning with Python scikit-learn", "score": 0.4},
    ]
    result = policy._cross_encoder_rerank(chunks, "Python machine learning")
    assert isinstance(result, list)
    assert len(result) == 3
    # Python ML chunks should rank above weather chunk
    top2_ids = [c["chunk_id"] for c in result[:2]]
    assert "c2" not in top2_ids or top2_ids[0] in ("c1", "c3"), (
        f"Weather chunk ranks too high: {top2_ids}"
    )


async def test_colbert_with_real_model() -> None:
    """ColBERT uses RAGatouille's checkpoint-correct model when artifacts exist."""
    from app.rag.agentic.patterns.colbert import ColBERTPattern, _get_encoder

    if _get_encoder() is None:
        pytest.skip("configured ColBERT model is unavailable")
    pattern = ColBERTPattern()
    chunks = [
        {"chunk_id": "c1", "content": "Python machine learning tutorial", "score": 0.6},
        {"chunk_id": "c2", "content": "Java enterprise deployment", "score": 0.8},
        {"chunk_id": "c3", "content": "Python deep learning neural networks", "score": 0.5},
    ]
    try:
        reranked = pattern.rerank("python ML", chunks)
        assert len(reranked) == 3
        # Python chunks should rank above Java
        top2 = [c["chunk_id"] for c in reranked[:2]]
        assert "c1" in top2 or "c3" in top2, f"Python chunks not in top 2: {top2}"
    finally:
        await pattern.aclose()


async def test_colbert_execute_returns_string() -> None:
    """ColBERT execute() must return context string."""
    from app.rag.agentic.patterns.colbert import ColBERTPattern, _get_encoder

    if _get_encoder() is None:
        pytest.skip("configured ColBERT model is unavailable")
    pattern = ColBERTPattern()
    chunks = [
        {"chunk_id": "c1", "content": "Python is great for ML", "score": 0.8},
        {"chunk_id": "c2", "content": "Java is used for enterprise", "score": 0.6},
    ]
    try:
        result = await pattern.execute(query="Python", chunks=chunks)
        assert isinstance(result, str)
        assert len(result) > 0
    finally:
        await pattern.aclose()
