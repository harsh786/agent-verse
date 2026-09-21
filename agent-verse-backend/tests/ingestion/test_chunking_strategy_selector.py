"""ChunkingStrategySelector — content-type dispatch, advanced overrides, and
per-strategy fallback branches.

Complements test_strategy_dispatch.py (which pins that advertised strategies
reach their real chunker) with full branch coverage of select/select_advanced/
is_advanced/select_and_chunk plus the legacy private per-strategy helpers.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.ingestion.chunking_strategy_selector import ChunkingStrategySelector
from app.ingestion.content_classifier import ContentType


class TestSelect:
    @pytest.mark.parametrize(
        ("content_type", "expected"),
        [
            (ContentType.TEXT, "semantic"),
            (ContentType.MARKDOWN, "heading"),
            (ContentType.PDF, "layout"),
            (ContentType.DOCX, "paragraph"),
            (ContentType.HTML, "dom"),
            (ContentType.CODE, "ast"),
            (ContentType.IMAGE, "region"),
            (ContentType.AUDIO, "timestamp"),
            (ContentType.VIDEO, "scene"),
            (ContentType.CSV, "row_group"),
            (ContentType.JSON, "record"),
            (ContentType.WEB_PAGE, "dom"),
            (ContentType.MIXED, "semantic"),
        ],
    )
    def test_default_strategy_per_content_type(
        self, content_type: ContentType, expected: str
    ) -> None:
        assert ChunkingStrategySelector().select(content_type) == expected

    def test_unmapped_content_type_falls_back_to_semantic(self) -> None:
        # EXCEL is a real ContentType but absent from _STRATEGY_MAP.
        assert ChunkingStrategySelector().select(ContentType.EXCEL) == "semantic"


class TestSelectAdvanced:
    def test_valid_collection_override_wins(self) -> None:
        sel = ChunkingStrategySelector()
        assert sel.select_advanced(ContentType.TEXT, "parent_child") == "parent_child"

    def test_invalid_collection_override_falls_back_to_default(self) -> None:
        sel = ChunkingStrategySelector()
        assert sel.select_advanced(ContentType.TEXT, "not_a_real_strategy") == "semantic"

    def test_none_override_falls_back_to_default(self) -> None:
        sel = ChunkingStrategySelector()
        assert sel.select_advanced(ContentType.PDF, None) == "layout"

    def test_empty_string_override_falls_back_to_default(self) -> None:
        sel = ChunkingStrategySelector()
        assert sel.select_advanced(ContentType.MARKDOWN, "") == "heading"


class TestIsAdvanced:
    @pytest.mark.parametrize(
        "strategy", ["parent_child", "sentence_window", "fixed", "agentic", "agentic_chunking"]
    )
    def test_advanced_strategies_recognized(self, strategy: str) -> None:
        assert ChunkingStrategySelector.is_advanced(strategy) is True

    @pytest.mark.parametrize("strategy", ["semantic", "heading", "layout", "unknown"])
    def test_non_advanced_strategies_rejected(self, strategy: str) -> None:
        assert ChunkingStrategySelector.is_advanced(strategy) is False


class TestSelectAndChunk:
    def test_dispatches_to_default_strategy_for_content_type(self) -> None:
        sel = ChunkingStrategySelector()
        with patch(
            "app.ingestion.chunkers.HeadingChunker.chunk",
            return_value=[type("C", (), {"content": "chunk1"})()],
        ) as m:
            result = sel.select_and_chunk("# Title\ntext", ContentType.MARKDOWN)
        assert m.called
        assert result == ["chunk1"]
        assert sel.last_strategy == "heading"

    def test_strategy_override_takes_precedence(self) -> None:
        sel = ChunkingStrategySelector()
        with patch(
            "app.ingestion.chunkers.ASTChunker.chunk",
            return_value=[type("C", (), {"content": "code chunk"})()],
        ):
            result = sel.select_and_chunk("plain text", ContentType.TEXT, strategy_override="ast")
        assert result == ["code chunk"]
        assert sel.last_strategy == "ast"

    def test_non_content_type_value_treated_as_text(self) -> None:
        sel = ChunkingStrategySelector()
        with patch(
            "app.ingestion.chunkers.SemanticChunker.chunk",
            return_value=[type("C", (), {"content": "semantic chunk"})()],
        ):
            result = sel.select_and_chunk("some text", "not-a-content-type")
        assert result == ["semantic chunk"]
        assert sel.last_strategy == "semantic"

    def test_empty_chunks_are_filtered_out(self) -> None:
        sel = ChunkingStrategySelector()
        blank = type("C", (), {"content": "   "})()
        real = type("C", (), {"content": "real content"})()
        with patch(
            "app.ingestion.chunkers.SemanticChunker.chunk", return_value=[blank, real]
        ):
            result = sel.select_and_chunk("text", ContentType.TEXT)
        assert result == ["real content"]

    def test_chunker_exception_falls_back_to_fixed_chunking(self) -> None:
        sel = ChunkingStrategySelector()
        with patch(
            "app.ingestion.chunkers.SemanticChunker.chunk",
            side_effect=RuntimeError("chunker exploded"),
        ):
            result = sel.select_and_chunk("word " * 10, ContentType.TEXT)
        assert result
        assert sel.last_strategy == "fixed"

    def test_get_chunker_for_strategy_exception_falls_back_to_fixed(self) -> None:
        sel = ChunkingStrategySelector()
        with patch(
            "app.ingestion.chunkers.get_chunker_for_strategy",
            side_effect=RuntimeError("no chunker available"),
        ):
            result = sel.select_and_chunk("some words here", ContentType.TEXT)
        assert result
        assert sel.last_strategy == "fixed"


class TestFixedChunk:
    def test_splits_long_text_into_overlapping_windows(self) -> None:
        sel = ChunkingStrategySelector()
        text = " ".join(f"word{i}" for i in range(1000))
        chunks = sel._fixed_chunk(text, size=400, overlap=50)
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.strip()

    def test_empty_text_returns_truncated_fallback(self) -> None:
        sel = ChunkingStrategySelector()
        result = sel._fixed_chunk("")
        assert result == [""]

    def test_short_text_returns_single_chunk(self) -> None:
        sel = ChunkingStrategySelector()
        result = sel._fixed_chunk("just a few words")
        assert result == ["just a few words"]


class TestLegacyPerStrategyHelpers:
    """Direct-invocation coverage for the private per-strategy chunk helpers.

    These are no longer reached via select_and_chunk (which dispatches through
    app.ingestion.chunkers.get_chunker_for_strategy instead), but they remain
    part of the class's public surface area and must behave correctly —
    including falling back to fixed-chunking on any chunker failure.
    """

    def test_semantic_chunk_success(self) -> None:
        sel = ChunkingStrategySelector()
        fake_chunk = type("C", (), {"content": "semantic text"})()
        with patch(
            "app.ingestion.chunkers.semantic.SemanticChunker.chunk",
            return_value=[fake_chunk],
        ):
            assert sel._semantic_chunk("text") == ["semantic text"]

    def test_semantic_chunk_falls_back_on_exception(self) -> None:
        sel = ChunkingStrategySelector()
        with patch(
            "app.ingestion.chunkers.semantic.SemanticChunker.chunk",
            side_effect=RuntimeError("boom"),
        ):
            result = sel._semantic_chunk("hello world")
        assert result == ["hello world"]

    def test_heading_chunk_success(self) -> None:
        sel = ChunkingStrategySelector()
        fake_chunk = type("C", (), {"content": "## Heading"})()
        with patch(
            "app.ingestion.chunkers.heading.HeadingChunker.chunk",
            return_value=[fake_chunk],
        ):
            assert sel._heading_chunk("# H\ntext") == ["## Heading"]

    def test_heading_chunk_falls_back_on_exception(self) -> None:
        sel = ChunkingStrategySelector()
        with patch(
            "app.ingestion.chunkers.heading.HeadingChunker.chunk",
            side_effect=RuntimeError("boom"),
        ):
            result = sel._heading_chunk("markdown text")
        assert result == ["markdown text"]

    def test_ast_chunk_success(self) -> None:
        sel = ChunkingStrategySelector()
        fake_chunk = type("C", (), {"content": "def f(): pass"})()
        with patch(
            "app.ingestion.chunkers.ast_chunker.ASTChunker.chunk", return_value=[fake_chunk]
        ):
            assert sel._ast_chunk("def f(): pass") == ["def f(): pass"]

    def test_ast_chunk_falls_back_on_exception(self) -> None:
        sel = ChunkingStrategySelector()
        with patch(
            "app.ingestion.chunkers.ast_chunker.ASTChunker.chunk",
            side_effect=RuntimeError("boom"),
        ):
            result = sel._ast_chunk("def f(): pass")
        assert result == ["def f(): pass"]

    def test_table_chunk_success(self) -> None:
        sel = ChunkingStrategySelector()
        fake_chunk = type("C", (), {"content": "row1,row2"})()
        with patch(
            "app.ingestion.chunkers.table.TableChunker.chunk", return_value=[fake_chunk]
        ):
            assert sel._table_chunk("a,b\n1,2") == ["row1,row2"]

    def test_table_chunk_falls_back_on_exception(self) -> None:
        sel = ChunkingStrategySelector()
        with patch(
            "app.ingestion.chunkers.table.TableChunker.chunk",
            side_effect=RuntimeError("boom"),
        ):
            result = sel._table_chunk("a,b\n1,2")
        # _fixed_chunk fallback splits on whitespace and rejoins with single
        # spaces, so newlines are not preserved.
        assert result == ["a,b 1,2"]

    def test_timestamp_chunk_success(self) -> None:
        sel = ChunkingStrategySelector()
        fake_chunk = type("C", (), {"content": "[00:00] hi"})()
        with patch(
            "app.ingestion.chunkers.timestamp.TimestampChunker.chunk",
            return_value=[fake_chunk],
        ):
            assert sel._timestamp_chunk("[00:00] hi") == ["[00:00] hi"]

    def test_timestamp_chunk_falls_back_on_exception(self) -> None:
        sel = ChunkingStrategySelector()
        with patch(
            "app.ingestion.chunkers.timestamp.TimestampChunker.chunk",
            side_effect=RuntimeError("boom"),
        ):
            result = sel._timestamp_chunk("[00:00] hi")
        assert result == ["[00:00] hi"]

    def test_scene_chunk_success(self) -> None:
        sel = ChunkingStrategySelector()
        fake_chunk = type("C", (), {"content": "Scene 1"})()
        with patch(
            "app.ingestion.chunkers.scene.SceneChunker.chunk", return_value=[fake_chunk]
        ):
            assert sel._scene_chunk("Scene 1 description") == ["Scene 1"]

    def test_scene_chunk_falls_back_on_exception(self) -> None:
        sel = ChunkingStrategySelector()
        with patch(
            "app.ingestion.chunkers.scene.SceneChunker.chunk",
            side_effect=RuntimeError("boom"),
        ):
            result = sel._scene_chunk("Scene 1 description")
        assert result == ["Scene 1 description"]


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
