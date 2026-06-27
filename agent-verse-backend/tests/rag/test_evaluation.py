"""Tests for RetrievalEvaluator."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.rag.evaluation import RetrievalEvaluator


@pytest.fixture
def mock_store():
    store = MagicMock()
    store.search = AsyncMock()
    return store


@pytest.mark.asyncio
async def test_perfect_retrieval_scores_1():
    store = MagicMock()
    store.search = AsyncMock(
        return_value=[
            {"chunk_id": "c1", "text": "Relevant chunk"},
            {"chunk_id": "c2", "text": "Also relevant"},
        ]
    )
    evaluator = RetrievalEvaluator(store)
    report = await evaluator.evaluate_collection(
        collection_id="col-1",
        test_queries=["What is the policy?"],
        expected_chunks=[["c1", "c2"]],
        k=2,
    )
    assert report.mean_precision == 1.0
    assert report.mean_recall == 1.0
    assert report.mean_mrr == 1.0


@pytest.mark.asyncio
async def test_zero_retrieval_scores_0():
    store = MagicMock()
    store.search = AsyncMock(
        return_value=[
            {"chunk_id": "c99", "text": "Irrelevant"},
        ]
    )
    evaluator = RetrievalEvaluator(store)
    report = await evaluator.evaluate_collection(
        collection_id="col-2",
        test_queries=["Find the answer"],
        expected_chunks=[["c1", "c2"]],
        k=1,
    )
    assert report.mean_precision == 0.0
    assert report.mean_recall == 0.0
    assert report.mean_mrr == 0.0
    assert len(report.recommendations) > 0


@pytest.mark.asyncio
async def test_query_count_mismatch_raises():
    store = MagicMock()
    evaluator = RetrievalEvaluator(store)
    with pytest.raises(ValueError, match="same length"):
        await evaluator.evaluate_collection(
            collection_id="col-3",
            test_queries=["Q1", "Q2"],
            expected_chunks=[["c1"]],
            k=5,
        )


@pytest.mark.asyncio
async def test_overall_score_is_weighted():
    store = MagicMock()
    store.search = AsyncMock(
        return_value=[
            {"chunk_id": "c1", "text": "chunk"},
        ]
    )
    evaluator = RetrievalEvaluator(store)
    report = await evaluator.evaluate_collection(
        collection_id="col-4",
        test_queries=["q"],
        expected_chunks=[["c1"]],
        k=1,
    )
    # precision=1.0, recall=1.0, mrr=1.0 → overall=1.0
    assert abs(report.overall_score - 1.0) < 0.01
