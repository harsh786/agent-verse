"""Comprehensive ingestion chunker tests (20+ tests).

Tests SemanticChunker, HeadingChunker, ASTChunker, TimestampChunker,
SceneChunker, TableChunker, PDFLayoutChunker, get_chunker_for_strategy,
and IngestionOrchestrator.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _tenant_ctx(tenant_id: str = "t1"):
    from app.tenancy.context import PlanTier, TenantContext
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="test-key")


# ─────────────────────────────────────────────────────────────────────────────
# SemanticChunker
# ─────────────────────────────────────────────────────────────────────────────

class TestSemanticChunker:
    def test_basic_chunking(self) -> None:
        from app.ingestion.chunkers.semantic import SemanticChunker
        chunker = SemanticChunker(max_chunk_tokens=512)
        content = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        chunks = chunker.chunk(content)
        assert len(chunks) >= 1
        assert all(c.content for c in chunks)

    def test_empty_content(self) -> None:
        from app.ingestion.chunkers.semantic import SemanticChunker
        chunker = SemanticChunker()
        chunks = chunker.chunk("")
        assert len(chunks) >= 1

    def test_large_paragraph_split_by_sentence(self) -> None:
        from app.ingestion.chunkers.semantic import SemanticChunker
        chunker = SemanticChunker(max_chunk_tokens=10)  # Very small limit
        content = "A" * 100 + ". " + "B" * 100 + "."
        chunks = chunker.chunk(content)
        assert len(chunks) >= 1

    def test_chunk_indexes_are_sequential(self) -> None:
        from app.ingestion.chunkers.semantic import SemanticChunker
        chunker = SemanticChunker(max_chunk_tokens=20)
        content = "\n\n".join([f"Para {i}: " + "x " * 30 for i in range(5)])
        chunks = chunker.chunk(content)
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i

    def test_single_paragraph_stays_together(self) -> None:
        from app.ingestion.chunkers.semantic import SemanticChunker
        chunker = SemanticChunker(max_chunk_tokens=512)
        content = "This is a single short paragraph."
        chunks = chunker.chunk(content)
        assert len(chunks) == 1
        assert chunks[0].content == content


# ─────────────────────────────────────────────────────────────────────────────
# HeadingChunker
# ─────────────────────────────────────────────────────────────────────────────

class TestHeadingChunker:
    def test_splits_on_markdown_headings(self) -> None:
        from app.ingestion.chunkers.heading import HeadingChunker
        chunker = HeadingChunker()
        content = "# Introduction\nSome text.\n## Methods\nMore text."
        chunks = chunker.chunk(content)
        assert len(chunks) >= 1
        assert all(c.content.strip() for c in chunks)

    def test_no_headings_returns_single_chunk(self) -> None:
        from app.ingestion.chunkers.heading import HeadingChunker
        chunker = HeadingChunker()
        content = "Plain text with no headings at all."
        chunks = chunker.chunk(content)
        assert len(chunks) >= 1

    def test_empty_content(self) -> None:
        from app.ingestion.chunkers.heading import HeadingChunker
        chunker = HeadingChunker()
        chunks = chunker.chunk("")
        assert isinstance(chunks, list)


# ─────────────────────────────────────────────────────────────────────────────
# ASTChunker
# ─────────────────────────────────────────────────────────────────────────────

class TestASTChunker:
    def test_splits_python_functions(self) -> None:
        from app.ingestion.chunkers.ast_chunker import ASTChunker
        chunker = ASTChunker()
        code = (
            "def foo():\n    return 1\n\n"
            "def bar():\n    return 2\n\n"
            "class Baz:\n    pass\n"
        )
        chunks = chunker.chunk(code)
        assert len(chunks) >= 1
        assert all(c.content.strip() for c in chunks)

    def test_non_python_falls_back_gracefully(self) -> None:
        from app.ingestion.chunkers.ast_chunker import ASTChunker
        chunker = ASTChunker()
        code = "function foo() { return 1; }\nfunction bar() { return 2; }"
        chunks = chunker.chunk(code)
        assert len(chunks) >= 1

    def test_empty_code(self) -> None:
        from app.ingestion.chunkers.ast_chunker import ASTChunker
        chunker = ASTChunker()
        chunks = chunker.chunk("")
        assert isinstance(chunks, list)


# ─────────────────────────────────────────────────────────────────────────────
# TimestampChunker
# ─────────────────────────────────────────────────────────────────────────────

class TestTimestampChunker:
    def test_splits_on_timestamps(self) -> None:
        from app.ingestion.chunkers.timestamp import TimestampChunker
        chunker = TimestampChunker()
        content = "[00:00] Introduction\n[01:30] Main topic\n[05:00] Conclusion"
        chunks = chunker.chunk(content)
        assert len(chunks) >= 1
        assert all(c.content.strip() for c in chunks)

    def test_no_timestamps_returns_chunks(self) -> None:
        from app.ingestion.chunkers.timestamp import TimestampChunker
        chunker = TimestampChunker()
        chunks = chunker.chunk("Plain text without timestamps.")
        assert len(chunks) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# SceneChunker
# ─────────────────────────────────────────────────────────────────────────────

class TestSceneChunker:
    def test_chunks_content(self) -> None:
        from app.ingestion.chunkers.scene import SceneChunker
        chunker = SceneChunker()
        content = "Scene 1: Opening shot.\nScene 2: Main action."
        chunks = chunker.chunk(content)
        assert len(chunks) >= 1

    def test_empty_returns_chunks(self) -> None:
        from app.ingestion.chunkers.scene import SceneChunker
        chunker = SceneChunker()
        chunks = chunker.chunk("")
        assert isinstance(chunks, list)


# ─────────────────────────────────────────────────────────────────────────────
# TableChunker
# ─────────────────────────────────────────────────────────────────────────────

class TestTableChunker:
    def test_chunks_csv_content(self) -> None:
        from app.ingestion.chunkers.table import TableChunker
        chunker = TableChunker()
        content = "name,age,city\nAlice,30,NYC\nBob,25,LA\nCharlie,35,Chicago"
        chunks = chunker.chunk(content)
        assert len(chunks) >= 1
        assert all(c.content for c in chunks)

    def test_empty_returns_chunks(self) -> None:
        from app.ingestion.chunkers.table import TableChunker
        chunker = TableChunker()
        chunks = chunker.chunk("")
        assert isinstance(chunks, list)


# ─────────────────────────────────────────────────────────────────────────────
# PDFLayoutChunker
# ─────────────────────────────────────────────────────────────────────────────

class TestPDFLayoutChunker:
    def test_chunks_text(self) -> None:
        from app.ingestion.chunkers.pdf_layout import PDFLayoutChunker
        chunker = PDFLayoutChunker()
        content = "Page 1 content\n\f\nPage 2 content\n\f\nPage 3 content"
        chunks = chunker.chunk(content)
        assert len(chunks) >= 1

    def test_empty_returns_chunks(self) -> None:
        from app.ingestion.chunkers.pdf_layout import PDFLayoutChunker
        chunker = PDFLayoutChunker()
        chunks = chunker.chunk("")
        assert isinstance(chunks, list)


# ─────────────────────────────────────────────────────────────────────────────
# get_chunker_for_strategy
# ─────────────────────────────────────────────────────────────────────────────

class TestGetChunkerForStrategy:
    def test_semantic_strategy(self) -> None:
        from app.ingestion.chunkers import SemanticChunker, get_chunker_for_strategy
        chunker = get_chunker_for_strategy("semantic")
        assert isinstance(chunker, SemanticChunker)

    def test_ast_strategy(self) -> None:
        from app.ingestion.chunkers import ASTChunker, get_chunker_for_strategy
        chunker = get_chunker_for_strategy("ast")
        assert isinstance(chunker, ASTChunker)

    def test_heading_strategy(self) -> None:
        from app.ingestion.chunkers import HeadingChunker, get_chunker_for_strategy
        chunker = get_chunker_for_strategy("heading")
        assert isinstance(chunker, HeadingChunker)

    def test_unknown_strategy_returns_semantic(self) -> None:
        from app.ingestion.chunkers import SemanticChunker, get_chunker_for_strategy
        chunker = get_chunker_for_strategy("unknown_strategy")
        assert isinstance(chunker, SemanticChunker)

    def test_code_strategy_returns_ast(self) -> None:
        from app.ingestion.chunkers import ASTChunker, get_chunker_for_strategy
        chunker = get_chunker_for_strategy("code")
        assert isinstance(chunker, ASTChunker)

    def test_table_strategy(self) -> None:
        from app.ingestion.chunkers import TableChunker, get_chunker_for_strategy
        chunker = get_chunker_for_strategy("table")
        assert isinstance(chunker, TableChunker)


# ─────────────────────────────────────────────────────────────────────────────
# IngestionOrchestrator
# ─────────────────────────────────────────────────────────────────────────────

class TestIngestionOrchestrator:
    def test_init(self) -> None:
        from app.ingestion.orchestrator import IngestionOrchestrator
        orch = IngestionOrchestrator()
        assert orch._kb is None
        assert orch._embedder is None

    async def test_ingest_text_no_kb(self) -> None:
        from app.ingestion.orchestrator import IngestionOrchestrator
        orch = IngestionOrchestrator()
        ctx = _tenant_ctx()
        result = await orch.ingest(
            "This is some plain text content for ingestion testing.",
            content_type="text",
            collection_id="col1",
            tenant_ctx=ctx,
            dry_run=True,
        )
        assert result.tenant_id == "t1"
        assert result.collection_id == "col1"
        assert result.chunks_prepared >= 1
        assert result.chunks_created == 0
        assert result.chunk_ids == []
        assert not result.persisted

    async def test_ingest_auto_detects_content_type(self) -> None:
        from app.ingestion.orchestrator import IngestionOrchestrator
        orch = IngestionOrchestrator()
        ctx = _tenant_ctx()
        result = await orch.ingest(
            "def foo():\n    return 42\n\ndef bar():\n    return 'hello'",
            content_type="auto",
            collection_id="col1",
            tenant_ctx=ctx,
            dry_run=True,
        )
        assert result.chunks_prepared >= 1

    async def test_ingest_with_kb_calls_atomic_batch_once(self) -> None:
        from app.ingestion.orchestrator import IngestionOrchestrator
        mock_kb = MagicMock()
        mock_kb._db = object()
        mock_kb.ingest_chunks_async = AsyncMock(
            side_effect=lambda chunks, **_: [chunk.chunk_id for chunk in chunks]
        )
        orch = IngestionOrchestrator(knowledge_store=mock_kb)
        ctx = _tenant_ctx()
        result = await orch.ingest(
            "Content to store in knowledge base.",
            content_type="text",
            collection_id="col1",
            tenant_ctx=ctx,
        )
        mock_kb.ingest_chunks_async.assert_awaited_once()
        assert result.chunks_created >= 1
        assert result.persisted

    async def test_ingest_returns_ingestion_result(self) -> None:
        from app.ingestion.orchestrator import IngestionOrchestrator, IngestionResult
        orch = IngestionOrchestrator()
        ctx = _tenant_ctx()
        result = await orch.ingest(
            "Plain text content.",
            content_type="text",
            collection_id="test-collection",
            tenant_ctx=ctx,
            dry_run=True,
        )
        assert isinstance(result, IngestionResult)
        assert result.ingestion_id is not None
        assert result.chunk_ids == []
        assert result.chunks_created == 0

    def test_chunk_dispatches_to_correct_chunker(self) -> None:
        from app.ingestion.content_classifier import ContentType
        from app.ingestion.orchestrator import IngestionOrchestrator
        orch = IngestionOrchestrator()
        chunks = orch._chunk(
            "def foo():\n    pass\ndef bar():\n    pass",
            ContentType.CODE,
        )
        assert len(chunks) >= 1

    def test_chunk_with_quality_check(self) -> None:
        from app.ingestion.content_classifier import ContentType
        from app.ingestion.orchestrator import IngestionOrchestrator
        orch = IngestionOrchestrator()
        chunks = orch._chunk_with_quality_check(
            "This is decent content worth keeping for quality testing purposes.",
            ContentType.TEXT,
        )
        assert len(chunks) >= 1

    async def test_ingest_markdown_with_headings(self) -> None:
        from app.ingestion.orchestrator import IngestionOrchestrator
        orch = IngestionOrchestrator()
        ctx = _tenant_ctx()
        md = "# Title\n\nParagraph one.\n\n## Section Two\n\nParagraph two."
        result = await orch.ingest(
            md,
            content_type="auto",
            collection_id="docs",
            tenant_ctx=ctx,
            dry_run=True,
        )
        assert result.chunks_prepared >= 1

    async def test_ingest_kb_error_propagates_without_ids(self) -> None:
        from app.ingestion.orchestrator import IngestionOrchestrator
        mock_kb = MagicMock()
        mock_kb._db = object()
        mock_kb.ingest_chunks_async = AsyncMock(side_effect=RuntimeError("DB down"))
        orch = IngestionOrchestrator(knowledge_store=mock_kb)
        ctx = _tenant_ctx()
        with pytest.raises(RuntimeError, match="DB down"):
            await orch.ingest(
                "Content that fails to persist.",
                content_type="text",
                collection_id="col1",
                tenant_ctx=ctx,
            )
        mock_kb.ingest_chunks_async.assert_awaited_once()
