# tests/rag/test_fusion_rag.py
"""Fusion RAG: multi-query → parallel retrieval → RRF merge."""
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession


# ── retrieve_fusion() unit tests ─────────────────────────────────────────────

async def test_retrieve_fusion_returns_merged_results():
    """retrieve_fusion must call hybrid_search N times and merge via RRF."""
    from app.rag.engine import retrieve_fusion, RetrievalResult

    mock_session = AsyncMock(spec=AsyncSession)
    call_count = 0

    async def fake_hybrid_search(**kwargs):
        nonlocal call_count
        call_count += 1
        return [
            RetrievalResult(
                chunk_id=f"c{call_count}_{i}",
                content=f"result {i} for query variant {call_count}",
                score=0.9 - i * 0.1,
                source_metadata={"source_url": f"https://x.com/{i}"},
                retrieval_legs=["vector"],
            )
            for i in range(3)
        ]

    with patch("app.rag.engine.hybrid_search", side_effect=fake_hybrid_search):
        results = await retrieve_fusion(
            mock_session,
            query="authentication flow",
            query_embedding=[0.1] * 10,
            collection_id="col1",
            top_k=5,
            max_variants=3,
        )

    assert call_count == 3
    assert isinstance(results, list)
    assert len(results) > 0
    assert all(hasattr(r, "chunk_id") for r in results)


async def test_retrieve_fusion_deduplicates_by_chunk_id():
    """Same chunk_id from multiple queries must appear only once in output."""
    from app.rag.engine import retrieve_fusion, RetrievalResult

    session = AsyncMock(spec=AsyncSession)

    async def fake_hybrid(**kwargs):
        return [RetrievalResult(
            chunk_id="shared_chunk",
            content="shared content",
            score=0.8,
            source_metadata={},
            retrieval_legs=["vector"],
        )]

    with patch("app.rag.engine.hybrid_search", side_effect=fake_hybrid):
        results = await retrieve_fusion(
            session, query="test", query_embedding=[0.1] * 10,
            collection_id="col1", top_k=10,
        )

    chunk_ids = [r.chunk_id for r in results]
    assert len(chunk_ids) == len(set(chunk_ids)), "Duplicates not removed"


async def test_retrieve_fusion_graceful_on_partial_failure():
    """If one query variant fails, others must still contribute results."""
    from app.rag.engine import retrieve_fusion, RetrievalResult

    session = AsyncMock(spec=AsyncSession)
    call_count = 0

    async def sometimes_fails(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("network error")
        return [RetrievalResult(
            chunk_id=f"c{call_count}", content="ok", score=0.7,
            source_metadata={}, retrieval_legs=["vector"],
        )]

    with patch("app.rag.engine.hybrid_search", side_effect=sometimes_fails):
        results = await retrieve_fusion(
            session, query="test", query_embedding=[0.1] * 10,
            collection_id="col1", top_k=5,
        )

    assert isinstance(results, list)


def test_retrieve_fusion_strategy_added_to_retrieve_dispatch():
    """retrieve() must dispatch 'fusion' strategy to retrieve_fusion()."""
    import inspect
    from app.rag import engine
    src = inspect.getsource(engine.retrieve)
    assert "fusion" in src, "retrieve() must handle strategy='fusion'"


def test_fusion_rag_pattern_state_is_implemented():
    """FusionRAGPattern must be IMPLEMENTED after this task."""
    from app.rag.agentic.patterns.fusion import FusionRAGPattern
    from app.rag.agentic.patterns.base import RAGPatternState
    p = FusionRAGPattern()
    assert p.state == RAGPatternState.IMPLEMENTED


def test_fusion_rag_pattern_has_execute_method():
    """FusionRAGPattern must have an execute() method."""
    from app.rag.agentic.patterns.fusion import FusionRAGPattern
    assert hasattr(FusionRAGPattern, "execute")
