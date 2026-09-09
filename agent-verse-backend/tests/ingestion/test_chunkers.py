"""Every content type must produce structured chunks with metadata."""
from __future__ import annotations

import pytest

from app.ingestion.chunkers import get_chunker_for_strategy
from app.ingestion.chunkers.ast_chunker import ASTChunker
from app.ingestion.chunkers.base import Chunk as ChunkerChunk
from app.ingestion.chunkers.base import ChunkerBase
from app.ingestion.chunkers.heading import HeadingChunker
from app.ingestion.chunkers.pdf_layout import PDFLayoutChunker
from app.ingestion.chunkers.scene import SceneChunker
from app.ingestion.chunkers.semantic import SemanticChunker
from app.ingestion.chunkers.table import TableChunker
from app.ingestion.chunkers.timestamp import TimestampChunker
from app.ingestion.content_classifier import ContentType


def test_semantic_chunker_splits_paragraphs():
    chunker = SemanticChunker(max_chunk_tokens=100)
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    chunks = chunker.chunk(text)
    assert len(chunks) >= 1
    assert all(isinstance(c, ChunkerChunk) for c in chunks)
    assert all(c.content for c in chunks)


def test_semantic_chunker_attaches_chunk_index():
    chunker = SemanticChunker()
    chunks = chunker.chunk("Para one.\n\nPara two.\n\nPara three.")
    for i, c in enumerate(chunks):
        assert c.chunk_index == i


def test_semantic_chunker_respects_max_tokens():
    chunker = SemanticChunker(max_chunk_tokens=20)
    long_text = " ".join(["word"] * 200)
    chunks = chunker.chunk(long_text)
    assert len(chunks) > 1


def test_ast_chunker_splits_python_functions():
    chunker = ASTChunker()
    code = "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n\nclass Calculator:\n    pass\n"
    chunks = chunker.chunk(code)
    assert len(chunks) >= 2
    contents = [c.content for c in chunks]
    assert any("def add" in c for c in contents)


def test_ast_chunker_metadata_has_symbol_type():
    chunker = ASTChunker()
    code = "def my_function():\n    pass\n\nclass MyClass:\n    pass"
    chunks = chunker.chunk(code)
    for c in chunks:
        assert "symbol_type" in c.metadata
        assert c.metadata["symbol_type"] in ("function", "class", "method", "module")


def test_ast_chunker_falls_back_on_invalid_code():
    chunker = ASTChunker()
    chunks = chunker.chunk("this is not valid python @@@")
    assert len(chunks) >= 1


def test_pdf_layout_chunker_page_aware():
    chunker = PDFLayoutChunker()
    text = "Page 1 content.\n--- PAGE 2 ---\nPage 2 content."
    chunks = chunker.chunk(text)
    assert len(chunks) >= 1
    for c in chunks:
        assert "page_number" in c.metadata or "section" in c.metadata


def test_heading_chunker_splits_on_markdown():
    chunker = HeadingChunker()
    text = "# Section 1\n\nContent 1.\n\n# Section 2\n\nContent 2."
    chunks = chunker.chunk(text)
    assert len(chunks) >= 2
    assert any("Section 1" in c.content for c in chunks)


def test_heading_chunker_metadata_has_heading():
    chunker = HeadingChunker()
    text = "# Introduction\n\nIntro.\n\n# Methods\n\nMethods."
    chunks = chunker.chunk(text)
    for c in chunks:
        assert "heading" in c.metadata


def test_timestamp_chunker_splits_by_time():
    chunker = TimestampChunker(chunk_duration_seconds=30)
    transcript = "[00:00:00] Welcome.\n[00:00:10] AI topic.\n[00:00:35] Safety.\n[00:01:10] Alignment.\n[00:02:00] End."
    chunks = chunker.chunk(transcript)
    assert len(chunks) >= 2


def test_timestamp_chunker_metadata_has_timestamps():
    chunker = TimestampChunker(chunk_duration_seconds=60)
    transcript = "[00:00:00] Start.\n[00:01:00] Middle.\n[00:02:00] End."
    chunks = chunker.chunk(transcript)
    for c in chunks:
        assert "start_time" in c.metadata


def test_scene_chunker_splits_on_scene_markers():
    chunker = SceneChunker()
    text = "[SCENE 1: 00:00-00:45] Opening.\n[SCENE 2: 00:45-02:30] Action.\n[SCENE 3: 02:30-05:00] Climax."
    chunks = chunker.chunk(text)
    assert len(chunks) >= 2


def test_table_chunker_groups_csv_rows():
    chunker = TableChunker(rows_per_chunk=3)
    csv = "name,age\nAlice,30\nBob,25\nCharlie,35\nDiana,28\nEve,32"
    chunks = chunker.chunk(csv)
    assert len(chunks) >= 2


def test_table_chunker_includes_header():
    chunker = TableChunker(rows_per_chunk=2)
    csv = "id,name\n1,Alice\n2,Bob\n3,Charlie\n4,Diana"
    chunks = chunker.chunk(csv)
    for c in chunks:
        assert "id,name" in c.content or "id" in c.content


def test_chunking_selector_maps_to_chunker_class():
    from app.ingestion.chunking_strategy_selector import ChunkingStrategySelector
    selector = ChunkingStrategySelector()
    for ct in ContentType:
        strategy = selector.select(ct)
        chunker = get_chunker_for_strategy(strategy)
        assert chunker is not None, f"No chunker for strategy '{strategy}' (content_type={ct.value})"
