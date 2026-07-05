"""Test HyDE, multi-hop, rerank retrieval strategies."""
import pytest
from app.rag.engine import RetrievalPlanner, retrieve, retrieve_hyde, retrieve_multi_hop, rerank_results


def test_planner_selects_hyde_for_abstract():
    assert RetrievalPlanner().select_strategy("what is machine learning") == "hyde"


def test_planner_selects_multi_hop_for_comparison():
    assert RetrievalPlanner().select_strategy("compare the performance of all agents") == "multi_hop"


def test_planner_selects_lexical_for_ids():
    assert RetrievalPlanner().select_strategy("find ticket JIRA-123") == "lexical"


def test_retrieve_dispatcher_exists():
    assert callable(retrieve)
    assert callable(retrieve_hyde)
    assert callable(retrieve_multi_hop)
    assert callable(rerank_results)


@pytest.mark.asyncio
async def test_rerank_results_returns_original_when_no_provider():
    from app.rag.engine import RetrievalResult
    results = [
        RetrievalResult(chunk_id=f"c{i}", content=f"content {i}", score=float(i)/10, source_metadata={})
        for i in range(5)
    ]
    reranked = await rerank_results(results, "test query", provider=None)
    assert len(reranked) == len(results)


@pytest.mark.asyncio
async def test_retrieve_hyde_falls_back_without_provider():
    from unittest.mock import AsyncMock, MagicMock
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(fetchall=lambda: []))
    result = await retrieve_hyde(
        session=session,
        query="what is machine learning",
        query_embedding=[0.1] * 10,
        collection_id="col1",
        provider=None,
        top_k=5,
    )
    assert isinstance(result, list)
