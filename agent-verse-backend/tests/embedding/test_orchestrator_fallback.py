"""Tests for Phase 8: EmbeddingOrchestrator fallback chain and batch embedding."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.embedding.orchestrator import EmbeddingOrchestrator


class OKProvider:
    provider_name = "ok-provider"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(i)] * 4 for i in range(len(texts))]


class FailProvider:
    provider_name = "fail-provider"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("provider down")


class TestEmbeddingFallbackChain:
    @pytest.mark.asyncio
    async def test_embed_with_fallback_uses_first_ok_provider(self) -> None:
        orch = EmbeddingOrchestrator()
        with patch("app.providers.base.embed_texts", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [[1.0, 2.0, 3.0]]
            result = await orch.embed_with_fallback(
                "test text",
                providers=[OKProvider()],
            )
        assert isinstance(result, list)
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_embed_with_fallback_skips_failing_provider(self) -> None:
        orch = EmbeddingOrchestrator()

        async def mock_embed(texts: list[str], provider: object) -> list[list[float]]:
            if isinstance(provider, FailProvider):
                raise RuntimeError("fail")
            return [[1.0, 0.0]]

        with patch("app.providers.base.embed_texts", side_effect=mock_embed):
            result = await orch.embed_with_fallback(
                "text",
                providers=[FailProvider(), OKProvider()],
            )
        assert result == [1.0, 0.0]

    @pytest.mark.asyncio
    async def test_embed_with_fallback_raises_when_all_fail(self) -> None:
        orch = EmbeddingOrchestrator()

        async def always_fail(texts: list[str], provider: object) -> list[list[float]]:
            raise RuntimeError("fail")

        with (
            patch("app.providers.base.embed_texts", side_effect=always_fail),
            pytest.raises(RuntimeError),
        ):
            await orch.embed_with_fallback("text", providers=[FailProvider()])


class TestEmbeddingBatch:
    @pytest.mark.asyncio
    async def test_embed_batch_returns_correct_count(self) -> None:
        orch = EmbeddingOrchestrator()
        texts = ["a", "b", "c", "d", "e"]

        async def mock_embed(ts: list[str], provider: object) -> list[list[float]]:
            return [[1.0] * 4 for _ in ts]

        with patch("app.providers.base.embed_texts", side_effect=mock_embed):
            result = await orch.embed_batch(texts, providers=[OKProvider()], batch_size=3)

        assert len(result.embeddings) == len(texts)

    @pytest.mark.asyncio
    async def test_embed_batch_empty_returns_empty(self) -> None:
        orch = EmbeddingOrchestrator()
        result = await orch.embed_batch([], providers=[OKProvider()])
        assert result.embeddings == []


class TestEmbedBatchFailedItemsNotZeroVectors:
    """D-12: embed_batch must NOT fill failed items with zero vectors.

    When all providers fail for a batch, the orchestrator must mark those
    items as ``None`` (not a zero vector) so callers can distinguish
    "embedding unavailable" from a real embedding.  A zero vector has
    cosine-similarity ~0 to everything, silently making the document
    unfindable in pgvector.
    """

    @pytest.mark.asyncio
    async def test_failed_batch_items_are_none_not_zero_vectors(self) -> None:
        """Regression: failed batch items must be None, never zero vectors."""
        orch = EmbeddingOrchestrator()
        texts = ["a", "b"]

        async def always_fail(ts: list[str], provider: object) -> list[list[float]]:
            raise RuntimeError("provider down")

        with patch("app.providers.base.embed_texts", side_effect=always_fail):
            result = await orch.embed_batch(
                texts,
                providers=[FailProvider()],
                batch_size=2,
            )

        # All items should be None, not zero vectors.
        for i, emb in enumerate(result.embeddings):
            assert emb is None, (
                f"Item {i} should be None for a failed embedding, "
                f"got {emb!r} (zero vector = silent index corruption)"
            )
        assert len(result.errors) > 0, "Errors should be recorded for failed batches"

    @pytest.mark.asyncio
    async def test_partial_batch_failure_marks_failed_as_none(self) -> None:
        """When the first batch succeeds but the second fails, only
        the second batch's items should be None."""
        orch = EmbeddingOrchestrator()
        texts = ["good-1", "good-2", "bad-1", "bad-2"]

        async def mock_embed(ts: list[str], provider: object) -> list[list[float]]:
            if any("bad" in t for t in ts):
                raise RuntimeError("provider down for bad batch")
            return [[1.0, 2.0, 3.0, 4.0] for _ in ts]

        with patch("app.providers.base.embed_texts", side_effect=mock_embed):
            result = await orch.embed_batch(
                texts,
                providers=[OKProvider()],
                batch_size=2,
            )

        # First batch succeeded
        assert result.embeddings[0] == [1.0, 2.0, 3.0, 4.0]
        assert result.embeddings[1] == [1.0, 2.0, 3.0, 4.0]
        # Second batch failed — must be None, not zero vectors
        assert result.embeddings[2] is None, (
            "Failed item should be None, not a zero vector"
        )
        assert result.embeddings[3] is None, (
            "Failed item should be None, not a zero vector"
        )

    @pytest.mark.asyncio
    async def test_failed_batch_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A failed batch must emit a WARNING log so operators can detect it."""
        orch = EmbeddingOrchestrator()

        async def always_fail(ts: list[str], provider: object) -> list[list[float]]:
            raise RuntimeError("total failure")

        with (
            patch("app.providers.base.embed_texts", side_effect=always_fail),
            caplog.at_level(logging.WARNING, logger="app.embedding.orchestrator"),
        ):
            await orch.embed_batch(
                ["a", "b"],
                providers=[FailProvider()],
                batch_size=2,
            )

        assert any(
            "zero vector" in r.message.lower() or "failed" in r.message.lower()
            for r in caplog.records
        ), "A warning should be logged when a batch embedding fails"
