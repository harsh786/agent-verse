"""Fusion embeds every query variant before concurrent retrieval."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.base import CompletionResponse, EmbedRequest, EmbedResponse
from app.rag.engine import RetrievalResult, retrieve_fusion


async def test_retrieve_fusion_dispatch_passes_embedder():
    """retrieve() with strategy=fusion must pass provider as embedder."""
    from app.rag.engine import retrieve

    session = AsyncMock(spec=AsyncSession)
    captured = {}

    async def fake_retrieve_fusion(session, *, embedder=None, **kwargs):
        captured["embedder"] = embedder
        return []

    mock_provider = AsyncMock()

    with patch("app.rag.engine.retrieve_fusion", side_effect=fake_retrieve_fusion):
        await retrieve(
            session,
            query="what is fusion",
            query_embedding=[0.1] * 10,
            collection_id="col1",
            strategy="fusion",
            provider=mock_provider,
        )

    assert captured.get("embedder") is mock_provider, \
        "embedder must be passed as provider to retrieve_fusion()"


async def test_fusion_embeds_every_variant_and_retrieves_concurrently() -> None:
    class Embedder:
        def __init__(self) -> None:
            self.texts: list[str] = []

        async def embed(self, request: EmbedRequest) -> EmbedResponse:
            self.texts.extend(request.texts)
            return EmbedResponse(embeddings=[[float(len(self.texts))]])

    provider = AsyncMock()
    provider.complete.return_value = CompletionResponse(
        content="variant two\nvariant three",
        model="tenant-model",
    )
    embedder = Embedder()
    active = 0
    max_active = 0
    calls: list[tuple[str, list[float] | None]] = []

    async def search(query: str, embedding: list[float] | None) -> list[RetrievalResult]:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        calls.append((query, embedding))
        await asyncio.sleep(0)
        active -= 1
        rankings = {
            "original query": ["a", "b", "c"],
            "variant two": ["b", "a", "c"],
            "variant three": ["b", "c", "a"],
        }
        return [
            RetrievalResult(
                chunk_id,
                f"content-{chunk_id}",
                1.0,
                {"source": query},
                ["vector"],
            )
            for chunk_id in rankings[query]
        ]

    results = await retrieve_fusion(
        None,
        query="original query",
        query_embedding=[999.0],
        collection_id="collection-1",
        provider=provider,
        model="tenant-model",
        embedder=embedder,
        strict=True,
        search_operation=search,
    )

    assert embedder.texts == ["original query", "variant two", "variant three"]
    assert calls == [
        ("original query", [1.0]),
        ("variant two", [2.0]),
        ("variant three", [3.0]),
    ]
    assert max_active == 3
    assert [result.chunk_id for result in results] == ["b", "a", "c"]
    assert [result.score for result in results] == pytest.approx(
        [
            1 / 62 + 1 / 61 + 1 / 61,
            1 / 61 + 1 / 62 + 1 / 63,
            1 / 63 + 1 / 63 + 1 / 62,
        ]
    )
    assert results[0].source_metadata["fusion_queries"] == [
        "original query",
        "variant two",
        "variant three",
    ]
    assert results[0].rrf_score > 0
    assert provider.complete.await_args.args[0].model == "tenant-model"
