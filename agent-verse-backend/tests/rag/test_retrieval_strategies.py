"""Test HyDE, multi-hop, rerank retrieval strategies."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.base import CompletionResponse, EmbedResponse
from app.rag.engine import (
    RetrievalPlanner,
    RetrievalStrategyExecutionError,
    rerank_results,
    retrieve,
    retrieve_hyde,
    retrieve_multi_hop,
)


def test_planner_selects_hyde_for_abstract():
    assert RetrievalPlanner().select_strategy("what is machine learning") == "hyde"


def test_planner_selects_multi_hop_for_comparison():
    assert (
        RetrievalPlanner().select_strategy("compare the performance of all agents")
        == "multi_hop"
    )


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
        RetrievalResult(
            chunk_id=f"c{i}",
            content=f"content {i}",
            score=float(i) / 10,
            source_metadata={},
        )
        for i in range(5)
    ]
    reranked = await rerank_results(results, "test query", provider=None)
    assert len(reranked) == len(results)


@pytest.mark.asyncio
async def test_retrieve_hyde_falls_back_without_provider():
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


@pytest.mark.asyncio
async def test_strict_hyde_requires_generated_document_embedding() -> None:
    provider = AsyncMock()
    provider.complete.return_value = CompletionResponse(
        content="generated hypothetical document",
        model="tenant-model",
    )
    embedder = AsyncMock()
    embedder.embed.return_value = EmbedResponse(embeddings=[[0.7, 0.8]])

    with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[])) as search:
        await retrieve_hyde(
            MagicMock(),
            query="sensitive original query",
            query_embedding=[0.1, 0.2],
            collection_id="collection-1",
            provider=provider,
            model="tenant-model",
            embedder=embedder,
            strict=True,
        )

    assert embedder.embed.await_args.args[0].texts == ["generated hypothetical document"]
    assert search.await_args.kwargs["query_embedding"] == [0.7, 0.8]
    assert search.await_args.kwargs["retrieval_mode"] == "vector"
    assert provider.complete.await_args.args[0].model == "tenant-model"


@pytest.mark.asyncio
async def test_strict_hyde_embedder_failure_is_explicit() -> None:
    provider = AsyncMock()
    provider.complete.return_value = CompletionResponse(content="document", model="model")
    embedder = AsyncMock()
    embedder.embed.side_effect = RuntimeError("embed unavailable")

    with pytest.raises(RetrievalStrategyExecutionError, match="embedding failed"):
        await retrieve_hyde(
            MagicMock(),
            query="query",
            query_embedding=[0.1],
            collection_id="collection-1",
            provider=provider,
            model="model",
            embedder=embedder,
            strict=True,
        )


@pytest.mark.asyncio
async def test_strict_hyde_provider_failure_is_explicit() -> None:
    provider = AsyncMock()
    provider.complete.side_effect = RuntimeError("provider unavailable")

    with pytest.raises(RetrievalStrategyExecutionError, match="generation failed"):
        await retrieve_hyde(
            MagicMock(),
            query="query",
            query_embedding=[0.1],
            collection_id="collection-1",
            provider=provider,
            model="model",
            embedder=AsyncMock(),
            strict=True,
        )


@pytest.mark.asyncio
async def test_strict_multi_hop_rejects_original_query_relabel() -> None:
    provider = AsyncMock()
    provider.complete.return_value = CompletionResponse(content='["original query"]', model="model")
    embedder = AsyncMock()

    with pytest.raises(RetrievalStrategyExecutionError, match="decomposition"):
        await retrieve_multi_hop(
            None,
            query="original query",
            query_embedding=[0.1],
            collection_id="collection-1",
            provider=provider,
            model="model",
            embedder=embedder,
            strict=True,
            search_operation=AsyncMock(),
        )

    embedder.embed.assert_not_awaited()
