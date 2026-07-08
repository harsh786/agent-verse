# tests/rag/test_memory_strategy.py
"""memory RAG strategy — LTM semantic recall."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession
from app.rag.engine import RetrievalResult


async def test_memory_strategy_calls_ltm_recall():
    """strategy='memory' must call LTM recall_async and return results."""
    from app.rag.engine import retrieve

    session = AsyncMock(spec=AsyncSession)

    # Mock LTM store
    mock_ltm = AsyncMock()
    mock_memory = MagicMock()
    mock_memory.memory_id = "m1"
    mock_memory.content = "Past experience: use OAuth before API calls"
    mock_memory.confidence = 0.85
    mock_memory.memory_type = "execution"
    mock_memory.source_goal_id = "g_old"
    mock_ltm.recall_async = AsyncMock(return_value=[mock_memory])

    mock_ctx = MagicMock()
    mock_ctx.tenant_id = "t1"

    results = await retrieve(
        session,
        query="how to authenticate with API",
        query_embedding=[0.1] * 10,
        collection_id="col1",
        strategy="memory",
        long_term_memory=mock_ltm,
        tenant_ctx=mock_ctx,
    )

    # Must call recall_async
    assert mock_ltm.recall_async.called
    # Must return the memory as RetrievalResult
    assert len(results) == 1
    assert results[0].source_metadata["source"] == "long_term_memory"
    assert "OAuth" in results[0].content


async def test_memory_strategy_returns_empty_without_ltm():
    """strategy='memory' without LTM store must return [] gracefully."""
    from app.rag.engine import retrieve
    session = AsyncMock(spec=AsyncSession)

    results = await retrieve(
        session,
        query="test",
        query_embedding=[0.1] * 10,
        collection_id="col1",
        strategy="memory",
        long_term_memory=None,  # No LTM
        tenant_ctx=None,
    )
    assert results == []


async def test_memory_strategy_graceful_on_ltm_error():
    """strategy='memory' must handle LTM errors gracefully."""
    from app.rag.engine import retrieve
    session = AsyncMock(spec=AsyncSession)

    mock_ltm = AsyncMock()
    mock_ltm.recall_async = AsyncMock(side_effect=Exception("DB error"))
    mock_ctx = MagicMock()
    mock_ctx.tenant_id = "t1"

    # Must not raise
    results = await retrieve(
        session,
        query="test",
        query_embedding=[0.1] * 10,
        collection_id="col1",
        strategy="memory",
        long_term_memory=mock_ltm,
        tenant_ctx=mock_ctx,
    )
    assert results == []


async def test_memory_results_have_correct_metadata():
    """Memory results must have source=long_term_memory in metadata."""
    from app.rag.engine import retrieve
    session = AsyncMock(spec=AsyncSession)

    mock_ltm = AsyncMock()
    mock_mem = MagicMock()
    mock_mem.memory_id = "m2"
    mock_mem.content = "Always use exponential backoff on rate limits"
    mock_mem.confidence = 0.9
    mock_mem.memory_type = "pattern"
    mock_mem.source_goal_id = "g1"
    mock_ltm.recall_async = AsyncMock(return_value=[mock_mem])

    mock_ctx = MagicMock()
    mock_ctx.tenant_id = "t1"

    results = await retrieve(
        session, query="rate limit handling",
        query_embedding=[0.1]*10, collection_id="c1",
        strategy="memory", long_term_memory=mock_ltm, tenant_ctx=mock_ctx,
    )

    assert results[0].retrieval_legs == ["long_term_memory"]
    assert results[0].score == 0.9
    assert results[0].chunk_id.startswith("ltm_")
