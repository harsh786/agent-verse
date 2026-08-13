"""Tests for Phase 1: Advanced chunking strategy wiring."""
from __future__ import annotations

import pytest
from app.ingestion.chunking_strategy_selector import (
    ChunkingStrategySelector,
    _ADVANCED_STRATEGIES,
)
from app.ingestion.content_classifier import ContentType


class TestChunkingStrategySelectorAdvanced:
    def setup_method(self) -> None:
        self.selector = ChunkingStrategySelector()

    def test_select_returns_default_for_text(self) -> None:
        assert self.selector.select(ContentType.TEXT) == "semantic"

    def test_select_advanced_returns_override_when_valid(self) -> None:
        for strategy in ("parent_child", "sentence_window", "fixed"):
            result = self.selector.select_advanced(ContentType.TEXT, strategy)
            assert result == strategy

    def test_select_advanced_ignores_invalid_override(self) -> None:
        result = self.selector.select_advanced(ContentType.TEXT, "nonexistent_strategy")
        assert result == "semantic"  # default for TEXT

    def test_select_advanced_with_no_override_uses_default(self) -> None:
        result = self.selector.select_advanced(ContentType.MARKDOWN, None)
        assert result == "heading"

    def test_is_advanced_true_for_known_strategies(self) -> None:
        for s in _ADVANCED_STRATEGIES:
            assert ChunkingStrategySelector.is_advanced(s) is True

    def test_is_advanced_false_for_standard_strategies(self) -> None:
        for s in ("semantic", "heading", "ast", "layout"):
            assert ChunkingStrategySelector.is_advanced(s) is False


class TestOrchestratorAdvancedChunking:
    def test_parent_child_chunking_dispatch(self) -> None:
        from app.ingestion.orchestrator import IngestionOrchestrator
        from app.ingestion.content_classifier import ContentType

        orch = IngestionOrchestrator()
        content = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph with more text."
        result = orch._chunk(content, ContentType.TEXT, strategy_override="parent_child")
        # Should return a non-empty list of strings
        assert isinstance(result, list)
        assert len(result) >= 1
        assert all(isinstance(c, str) for c in result)

    def test_sentence_window_chunking_dispatch(self) -> None:
        from app.ingestion.orchestrator import IngestionOrchestrator
        from app.ingestion.content_classifier import ContentType

        orch = IngestionOrchestrator()
        content = "First sentence here. Second sentence follows. Third sentence here. Fourth one."
        result = orch._chunk(content, ContentType.TEXT, strategy_override="sentence_window")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_fixed_chunking_dispatch(self) -> None:
        from app.ingestion.orchestrator import IngestionOrchestrator
        from app.ingestion.content_classifier import ContentType

        orch = IngestionOrchestrator()
        content = "A" * 500  # 500 chars of repeated A
        result = orch._chunk(content, ContentType.TEXT, strategy_override="fixed")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_no_override_uses_default(self) -> None:
        from app.ingestion.orchestrator import IngestionOrchestrator
        from app.ingestion.content_classifier import ContentType

        orch = IngestionOrchestrator()
        content = "## Heading\n\nBody text under heading."
        result = orch._chunk(content, ContentType.MARKDOWN)
        assert isinstance(result, list)
        assert len(result) >= 1
