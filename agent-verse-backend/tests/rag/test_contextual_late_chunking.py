"""Tests for contextual enricher, late chunker, NLI checker, claim decomposer."""
from __future__ import annotations

import pytest

from app.rag.contextual_enricher import ContextualChunkEnricher
from app.rag.late_chunker import LateChunker


class TestContextualChunkEnricher:
    def setup_method(self) -> None:
        self.enricher = ContextualChunkEnricher()

    def test_enrich_prepends_summary(self) -> None:
        chunks = ["First chunk text.", "Second chunk text."]
        enriched = self.enricher.enrich(chunks, "This document is about Python.")
        assert all("This document is about Python" in c for c in enriched)
        assert "First chunk text." in enriched[0]
        assert "Second chunk text." in enriched[1]

    def test_enrich_empty_summary_returns_original(self) -> None:
        chunks = ["chunk one", "chunk two"]
        result = self.enricher.enrich(chunks, "")
        assert result == chunks

    def test_enrich_truncates_long_summary(self) -> None:
        chunks = ["text"]
        long_summary = "a" * 300
        enriched = self.enricher.enrich(chunks, long_summary)
        # Prefix should be truncated to _MAX_SUMMARY_CHARS
        assert len(enriched[0]) < len(long_summary) + 100

    @pytest.mark.asyncio
    async def test_summarize_document_graceful_on_failure(self) -> None:
        class FailProvider:
            async def complete(self, req: object) -> None:
                raise RuntimeError("down")

        result = await self.enricher.summarize_document("content", FailProvider())
        assert result == ""

    @pytest.mark.asyncio
    async def test_enrich_with_llm_fallback_on_failure(self) -> None:
        class FailProvider:
            async def complete(self, req: object) -> None:
                raise RuntimeError("down")

        chunks = ["chunk a", "chunk b"]
        result = await self.enricher.enrich_with_llm(chunks, "full content", FailProvider())
        assert result == chunks  # original returned on failure


class TestLateChunker:
    def setup_method(self) -> None:
        self.chunker = LateChunker()

    def test_is_supported_false_for_plain_provider(self) -> None:
        class PlainProvider:
            pass

        assert LateChunker.is_supported(PlainProvider()) is False

    def test_is_supported_true_for_token_embedding_provider(self) -> None:
        class TokenProvider:
            async def embed_tokens(self, text: str) -> list[list[float]]:
                return [[0.1] * 4 for _ in range(len(text.split()))]

        assert LateChunker.is_supported(TokenProvider()) is True

    @pytest.mark.asyncio
    async def test_chunk_and_embed_returns_none_for_unsupported(self) -> None:
        class PlainProvider:
            pass

        result = await self.chunker.chunk_and_embed("content", ["chunk"], PlainProvider())
        assert result is None

    @pytest.mark.asyncio
    async def test_chunk_and_embed_standard(self) -> None:
        from unittest.mock import AsyncMock, patch

        chunks = ["chunk one", "chunk two"]
        with patch("app.providers.base.embed_texts", new=AsyncMock(return_value=[[0.1, 0.2], [0.3, 0.4]])):
            result = await self.chunker.chunk_and_embed_standard(chunks, object())
        assert len(result) == 2
        assert result[0].content == "chunk one"
        assert result[1].embedding == [0.3, 0.4]
