"""Isolated tests for HyDE (Hypothetical Document Embeddings) retrieval.

The HyDE code path spans app/rag/engine.py::retrieve_hyde (the actual
generate -> embed -> vector-search algorithm), app/rag/gateway.py (wires it
into the certified RAGStrategy.HYDE strategy with strict=True), and
app/rag/agentic/retrieval_policy.py (just the RetrievalPolicy.HYDE enum
member). tests/rag/test_retrieval_engine.py::TestRetrieveHyde already covers
several non-strict/strict failure permutations; these tests focus on the
success path and behaviours not yet covered there, isolated from the rest of
the retrieval engine's test surface.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.providers.base import CompletionResponse, EmbedResponse
from app.rag.engine import RetrievalResult, RetrievalStrategyExecutionError, retrieve_hyde


@pytest.mark.asyncio
async def test_successful_generation_embeds_and_searches_with_hypothetical_document() -> None:
    """Happy path: the LLM-generated hypothetical document is what gets
    embedded and passed to the underlying vector search -- not the raw
    user query -- and the generated embedding is used, not the original
    query_embedding."""
    provider = AsyncMock()
    provider.complete.return_value = CompletionResponse(
        content="The retention policy requires records to be kept for seven years.",
        model="m",
    )
    embedder = AsyncMock()
    embedder.embed.return_value = EmbedResponse(embeddings=[[0.9, 0.8, 0.7]], model="embed-model")
    seed_embedding = [0.1, 0.1]

    with patch(
        "app.rag.engine.hybrid_search",
        AsyncMock(
            return_value=[
                RetrievalResult("hit-1", "Matched via hypothetical doc.", 0.95, {}, ["vector"])
            ]
        ),
    ) as hybrid:
        out = await retrieve_hyde(
            AsyncMock(),
            query="what is the retention policy",
            query_embedding=seed_embedding,
            collection_id="col-1",
            provider=provider,
            model="m",
            top_k=5,
            embedder=embedder,
            strict=True,
        )

    assert [r.chunk_id for r in out] == ["hit-1"]
    hybrid.assert_awaited_once()
    call_kwargs = hybrid.await_args.kwargs
    assert call_kwargs["query"] == (
        "The retention policy requires records to be kept for seven years."
    )
    assert call_kwargs["query_embedding"] == [0.9, 0.8, 0.7]
    assert call_kwargs["query_embedding"] != seed_embedding
    assert call_kwargs["retrieval_mode"] == "vector"
    assert call_kwargs["top_k"] == 5


@pytest.mark.asyncio
async def test_successful_generation_records_strategy_evidence() -> None:
    """When a strategy_evidence dict is supplied (as gateway.py does), the
    generated document's hash and generation metadata must be recorded for
    observability/trace purposes."""
    provider = AsyncMock()
    provider.complete.return_value = CompletionResponse(content="A hypothetical answer.", model="m")
    embedder = AsyncMock()
    embedder.embed.return_value = EmbedResponse(embeddings=[[0.5]], model="embed-model")
    evidence: dict[str, object] = {}

    with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[])):
        await retrieve_hyde(
            AsyncMock(),
            query="what is x",
            query_embedding=[0.1],
            collection_id="col-1",
            provider=provider,
            model="m",
            embedder=embedder,
            strict=True,
            strategy_evidence=evidence,
        )

    assert evidence["generation"] == "hypothetical_document"
    assert evidence["model"] == "m"
    assert "generated_text_sha256" in evidence


@pytest.mark.asyncio
async def test_no_embedder_falls_back_to_original_query_embedding() -> None:
    """When no embedder is supplied, the caller's query_embedding is reused
    verbatim for the vector search against the generated document."""
    provider = AsyncMock()
    provider.complete.return_value = CompletionResponse(content="A hypothetical answer.", model="m")
    seed_embedding = [0.2, 0.3]

    with patch(
        "app.rag.engine.hybrid_search", AsyncMock(return_value=[])
    ) as hybrid:
        await retrieve_hyde(
            AsyncMock(),
            query="what is x",
            query_embedding=seed_embedding,
            collection_id="col-1",
            provider=provider,
            model="m",
            embedder=None,
            strict=False,
        )

    assert hybrid.await_args.kwargs["query_embedding"] == seed_embedding


@pytest.mark.asyncio
async def test_generation_failure_falls_back_to_standard_retrieval_not_a_crash() -> None:
    """Non-strict mode: an LLM failure while generating the hypothetical
    document must fall back to a normal hybrid search on the ORIGINAL
    query, not raise."""
    provider = AsyncMock()
    provider.complete.side_effect = TimeoutError("LLM provider timed out")

    with patch(
        "app.rag.engine.hybrid_search",
        AsyncMock(
            return_value=[RetrievalResult("fallback-1", "Direct match.", 0.5, {}, ["vector"])]
        ),
    ) as hybrid:
        out = await retrieve_hyde(
            AsyncMock(),
            query="what is the retention policy",
            query_embedding=[0.1],
            collection_id="col-1",
            provider=provider,
            model="m",
            strict=False,
        )

    assert [r.chunk_id for r in out] == ["fallback-1"]
    hybrid.assert_awaited_once()
    assert hybrid.await_args.kwargs["query"] == "what is the retention policy"


@pytest.mark.asyncio
async def test_generation_failure_strict_raises_instead_of_falling_back() -> None:
    provider = AsyncMock()
    provider.complete.side_effect = TimeoutError("LLM provider timed out")

    with pytest.raises(RetrievalStrategyExecutionError, match="generation failed"):
        await retrieve_hyde(
            AsyncMock(),
            query="what is the retention policy",
            query_embedding=[0.1],
            collection_id="col-1",
            provider=provider,
            model="m",
            embedder=AsyncMock(),
            strict=True,
        )


@pytest.mark.asyncio
async def test_ambiguous_underspecified_query_still_generates_hypothetical_document() -> None:
    """A very short/ambiguous query (the kind HyDE is meant for -- e.g. an
    abstract "what is" question with no concrete nouns) must still be sent
    to the LLM for hypothetical-document generation rather than being
    special-cased or skipped."""
    provider = AsyncMock()
    provider.complete.return_value = CompletionResponse(
        content="A short hypothetical answer about it.", model="m"
    )
    embedder = AsyncMock()
    embedder.embed.return_value = EmbedResponse(embeddings=[[0.4]], model="embed-model")

    with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[])) as hybrid:
        await retrieve_hyde(
            AsyncMock(),
            query="what is it",
            query_embedding=[0.1],
            collection_id="col-1",
            provider=provider,
            model="m",
            embedder=embedder,
            strict=True,
        )

    provider.complete.assert_awaited_once()
    sent_prompt = provider.complete.await_args.args[0]
    assert "what is it" in sent_prompt.messages[-1].content
    hybrid.assert_awaited_once()
    assert hybrid.await_args.kwargs["query"] == "A short hypothetical answer about it."


@pytest.mark.asyncio
async def test_embedding_failure_falls_back_to_standard_retrieval_when_not_strict() -> None:
    provider = AsyncMock()
    provider.complete.return_value = CompletionResponse(content="A hypothetical answer.", model="m")
    embedder = AsyncMock()
    embedder.embed.side_effect = RuntimeError("embedding service down")

    with patch(
        "app.rag.engine.hybrid_search",
        AsyncMock(return_value=[RetrievalResult("fallback-1", "Direct match.", 0.5, {}, ["vector"])]),
    ) as hybrid:
        out = await retrieve_hyde(
            AsyncMock(),
            query="what is the retention policy",
            query_embedding=[0.1],
            collection_id="col-1",
            provider=provider,
            model="m",
            embedder=embedder,
            strict=False,
        )

    assert [r.chunk_id for r in out] == ["fallback-1"]
    # Fallback hybrid_search call uses the original query, since the
    # hypothetical-document path failed before a search could run.
    assert hybrid.await_args.kwargs["query"] == "what is the retention policy"
