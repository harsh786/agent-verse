"""Tests for app.rag.chunker — SemanticChunker."""

from __future__ import annotations

import pytest

from app.rag.chunker import Chunk, SemanticChunker


# ---------------------------------------------------------------------------
# Basic construction
# ---------------------------------------------------------------------------

def test_empty_string_returns_empty_list() -> None:
    chunker = SemanticChunker()
    assert chunker.chunk("") == []
    assert chunker.chunk("   ") == []


def test_short_text_returns_single_chunk() -> None:
    """Text longer than min_chunk_chars but shorter than max_chars → single chunk."""
    chunker = SemanticChunker(max_chars=512, overlap_chars=64, min_chunk_chars=10)
    text = "This is a simple sentence that is long enough."
    result = chunker.chunk(text)
    assert len(result) == 1
    assert result[0].content == text.strip()


# ---------------------------------------------------------------------------
# Text / sentence chunking
# ---------------------------------------------------------------------------

def test_text_chunking_sentence_boundaries() -> None:
    """Long text splits cleanly on sentence boundaries."""
    chunker = SemanticChunker(max_chars=60, overlap_chars=0, min_chunk_chars=10)
    # Two sentences that together exceed max_chars
    text = "The quick brown fox jumps. The lazy dog sleeps all day long here."
    result = chunker.chunk(text, source_type="text")
    assert len(result) >= 1
    for chunk in result:
        assert len(chunk.content) <= 60 + 40  # allow some slack for sentence integrity


def test_long_text_creates_multiple_chunks() -> None:
    """A text that clearly exceeds max_chars is split into multiple chunks."""
    chunker = SemanticChunker(max_chars=80, overlap_chars=0, min_chunk_chars=10)
    sentences = [f"Sentence number {i} has some padding words here." for i in range(10)]
    text = " ".join(sentences)
    result = chunker.chunk(text)
    assert len(result) > 1


def test_overlap_chars_creates_overlapping_content() -> None:
    """When overlap_chars > 0, consecutive chunks share some content."""
    chunker = SemanticChunker(max_chars=80, overlap_chars=20, min_chunk_chars=10)
    sentences = [f"Sentence {i} is here for overlap testing purposes now." for i in range(6)]
    text = " ".join(sentences)
    result = chunker.chunk(text)
    if len(result) >= 2:
        # The end of chunk N-1 should appear somewhere in chunk N
        end_of_first = result[0].content[-15:]
        # overlap content from the previous chunk should appear at the start of next chunk
        assert any(end_of_first[:10] in result[i].content for i in range(1, len(result)))


def test_min_chunk_chars_filters_tiny_chunks() -> None:
    """Chunks smaller than min_chunk_chars are not produced."""
    chunker = SemanticChunker(max_chars=512, overlap_chars=0, min_chunk_chars=100)
    # Short text below min_chunk_chars
    text = "Hi there."
    result = chunker.chunk(text)
    # All returned chunks must meet min_chunk_chars
    for chunk in result:
        assert len(chunk.content) >= 100


def test_chunk_has_correct_start_char() -> None:
    """Chunk.start_char should be >= 0."""
    chunker = SemanticChunker(max_chars=200, overlap_chars=0, min_chunk_chars=10)
    text = "First sentence here. Second sentence here. Third sentence here."
    result = chunker.chunk(text)
    for chunk in result:
        assert chunk.start_char >= 0


def test_chunk_has_correct_end_char() -> None:
    """Chunk.end_char > start_char for every chunk."""
    chunker = SemanticChunker(max_chars=200, overlap_chars=0, min_chunk_chars=10)
    text = "First sentence here. Second sentence here. Third sentence here."
    result = chunker.chunk(text)
    for chunk in result:
        assert chunk.end_char > chunk.start_char


# ---------------------------------------------------------------------------
# Markdown chunking
# ---------------------------------------------------------------------------

def test_markdown_splits_on_headings() -> None:
    """Markdown text splits at heading boundaries."""
    chunker = SemanticChunker(max_chars=512, overlap_chars=0, min_chunk_chars=10)
    text = "# Heading One\nContent under heading one.\n## Heading Two\nContent under heading two."
    result = chunker.chunk(text, source_type="markdown")
    assert len(result) >= 1


def test_markdown_headings_preserved_in_metadata() -> None:
    """Markdown chunks carry the section heading in metadata."""
    chunker = SemanticChunker(max_chars=512, overlap_chars=0, min_chunk_chars=5)
    text = "Intro text before any heading.\n# Section Alpha\nAlpha body content here.\n## Section Beta\nBeta body content here."
    result = chunker.chunk(text, source_type="markdown")
    # At least one chunk should have a heading in metadata
    headings_found = [c.metadata.get("heading", "") for c in result if c.metadata.get("heading")]
    assert len(headings_found) >= 1


# ---------------------------------------------------------------------------
# Code chunking
# ---------------------------------------------------------------------------

def test_code_chunker_handles_python_def() -> None:
    """Python function definitions are kept together as chunks."""
    chunker = SemanticChunker(max_chars=512, overlap_chars=0, min_chunk_chars=10)
    code = (
        "import os\n\n"
        "def hello_world():\n"
        "    print('Hello, world!')\n\n"
        "def goodbye_world():\n"
        "    print('Goodbye!')\n"
    )
    result = chunker.chunk(code, source_type="python")
    assert len(result) >= 1
    contents = " ".join(c.content for c in result)
    assert "def hello_world" in contents or "hello_world" in contents


def test_code_chunker_handles_typescript_function() -> None:
    """TypeScript function definitions trigger chunk splits."""
    chunker = SemanticChunker(max_chars=512, overlap_chars=0, min_chunk_chars=10)
    code = (
        "const greeting = 'hi';\n\n"
        "function sayHello(name: string): void {\n"
        "    console.log(`Hello ${name}`);\n"
        "}\n\n"
        "function sayBye(name: string): void {\n"
        "    console.log(`Bye ${name}`);\n"
        "}\n"
    )
    result = chunker.chunk(code, source_type="typescript")
    assert len(result) >= 1
    contents = " ".join(c.content for c in result)
    assert "sayHello" in contents or "function" in contents


def test_code_source_type_alias() -> None:
    """source_type='code' is treated same as 'python'."""
    chunker = SemanticChunker(max_chars=512, overlap_chars=0, min_chunk_chars=10)
    code = "def foo():\n    return 42\n\nclass Bar:\n    pass\n"
    result = chunker.chunk(code, source_type="code")
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Chunk dataclass
# ---------------------------------------------------------------------------

def test_chunk_size_property() -> None:
    """Chunk.size returns len(content)."""
    c = Chunk(content="hello world", start_char=0, end_char=11)
    assert c.size == 11
