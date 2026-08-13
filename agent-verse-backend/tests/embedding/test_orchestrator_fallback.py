"""Tests for Phase 8: EmbeddingOrchestrator fallback chain and batch embedding."""
from __future__ import annotations

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
        from unittest.mock import AsyncMock, patch

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
        from unittest.mock import AsyncMock, patch

        orch = EmbeddingOrchestrator()
        call_count = {"n": 0}

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
        from unittest.mock import AsyncMock, patch

        orch = EmbeddingOrchestrator()

        async def always_fail(texts: list[str], provider: object) -> list[list[float]]:
            raise RuntimeError("fail")

        with patch("app.providers.base.embed_texts", side_effect=always_fail):
            with pytest.raises(RuntimeError):
                await orch.embed_with_fallback("text", providers=[FailProvider()])


class TestEmbeddingBatch:
    @pytest.mark.asyncio
    async def test_embed_batch_returns_correct_count(self) -> None:
        from unittest.mock import AsyncMock, patch

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
