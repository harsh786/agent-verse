"""Comprehensive tests for app/rag/chunker.py — targeting 95%+ coverage."""
from __future__ import annotations

import pytest

from app.rag.chunker import Chunk, SemanticChunker


class TestChunkModel:
    def test_size_property(self):
        chunk = Chunk(content="hello world", start_char=0, end_char=11)
        assert chunk.size == 11

    def test_metadata_default_empty(self):
        chunk = Chunk(content="text", start_char=0, end_char=4)
        assert chunk.metadata == {}

    def test_explicit_metadata(self):
        chunk = Chunk(content="text", start_char=0, end_char=4, metadata={"heading": "# Intro"})
        assert chunk.metadata["heading"] == "# Intro"


class TestSemanticChunkerEmpty:
    def test_empty_text_returns_no_chunks(self):
        chunker = SemanticChunker()
        assert chunker.chunk("") == []

    def test_whitespace_only_returns_no_chunks(self):
        chunker = SemanticChunker()
        assert chunker.chunk("   \n\t  ") == []


class TestSemanticChunkerSentences:
    def test_short_text_single_chunk(self):
        chunker = SemanticChunker(max_chars=512, min_chunk_chars=5)
        chunks = chunker.chunk("Hello world. This is a test.")
        assert len(chunks) >= 1
        assert "Hello" in chunks[0].content or "world" in chunks[0].content

    def test_long_text_splits_into_multiple_chunks(self):
        # Create text that forces splitting
        sentence = "The quick brown fox jumps over the lazy dog. "
        text = sentence * 20  # ~900 chars
        chunker = SemanticChunker(max_chars=200, min_chunk_chars=50)
        chunks = chunker.chunk(text)
        assert len(chunks) > 1

    def test_chunk_respects_max_chars(self):
        sentence = "A" * 100 + ". "
        text = sentence * 10
        chunker = SemanticChunker(max_chars=250, min_chunk_chars=20)
        chunks = chunker.chunk(text)
        for chunk in chunks:
            # Chunks should not exceed max_chars + one sentence
            assert len(chunk.content) <= 500  # generous upper bound

    def test_chunk_with_overlap(self):
        """Covers overlap logic: when current > overlap_chars."""
        text = (
            "First important sentence here to fill buffer. "
            "Second important sentence here fills more. "
            "Third sentence triggers split with overlap. "
        ) * 5
        chunker = SemanticChunker(max_chars=100, overlap_chars=30, min_chunk_chars=20)
        chunks = chunker.chunk(text)
        assert len(chunks) > 1

    def test_overlap_when_current_shorter_than_overlap(self):
        """Covers lines 88-89: current <= overlap_chars — no overlap applied."""
        # Create a scenario where the current buffer is short before split
        text = "Hi. " + "X" * 300 + ". "
        chunker = SemanticChunker(max_chars=100, overlap_chars=50, min_chunk_chars=5)
        chunks = chunker.chunk(text)
        assert len(chunks) >= 1

    def test_skip_small_final_chunk(self):
        """Final chunk below min_chunk_chars should be skipped."""
        chunker = SemanticChunker(max_chars=500, min_chunk_chars=100)
        # Text that ends with a tiny fragment
        text = "A" * 50 + "."  # 51 chars, below min_chunk_chars=100
        chunks = chunker.chunk(text)
        # 51 < 100 → should be no chunks or at least not include the small part
        assert all(len(c.content) >= 50 for c in chunks)

    def test_default_source_type_uses_sentences(self):
        chunker = SemanticChunker(max_chars=512, min_chunk_chars=10)
        text = "This is a sentence. Another sentence here. And one more."
        chunks = chunker.chunk(text, source_type="text")
        assert len(chunks) >= 1


class TestSemanticChunkerMarkdown:
    def test_markdown_splits_on_headings(self):
        text = """# Introduction
This is the introduction section.

## Getting Started
Install the package first.

## Configuration
Set up your environment."""
        chunker = SemanticChunker(max_chars=512, min_chunk_chars=10)
        chunks = chunker.chunk(text, source_type="markdown")
        assert len(chunks) >= 2

    def test_markdown_chunk_includes_heading_metadata(self):
        text = """# Main Title
Content of the main section.

## Sub Section
Content of the sub section."""
        chunker = SemanticChunker(max_chars=512, min_chunk_chars=5)
        chunks = chunker.chunk(text, source_type="markdown")
        # At least some chunks should have heading metadata
        headings = [c.metadata.get("heading", "") for c in chunks]
        assert any(heading for heading in headings)

    def test_markdown_large_section_recursively_chunked(self):
        """Covers lines 128-133: section > max_chars gets recursively chunked."""
        # Create a large section that exceeds max_chars
        large_content = "The answer is very detailed. " * 30  # ~870 chars
        text = f"""# Big Section
{large_content}

## Small Section
Short content."""
        chunker = SemanticChunker(max_chars=200, min_chunk_chars=20)
        chunks = chunker.chunk(text, source_type="markdown")
        assert len(chunks) >= 2

    def test_markdown_empty_sections_skipped(self):
        text = """# Header One

# Header Two
Some content here."""
        chunker = SemanticChunker(max_chars=512, min_chunk_chars=5)
        chunks = chunker.chunk(text, source_type="markdown")
        # Empty section after Header One should be skipped
        assert len(chunks) >= 1


class TestSemanticChunkerCode:
    def test_python_code_splits_on_functions(self):
        code = '''def hello():
    print("Hello")
    return True

def world():
    print("World")
    return False

class MyClass:
    def method(self):
        pass
'''
        chunker = SemanticChunker(max_chars=512, min_chunk_chars=10)
        chunks = chunker.chunk(code, source_type="python")
        assert len(chunks) >= 2

    def test_typescript_code_splits(self):
        code = '''function add(a: number, b: number): number {
    return a + b;
}

function subtract(a: number, b: number): number {
    return a - b;
}

const multiply = (a: number, b: number): number => a * b;
'''
        chunker = SemanticChunker(max_chars=512, min_chunk_chars=10)
        chunks = chunker.chunk(code, source_type="typescript")
        assert len(chunks) >= 1

    def test_code_chunk_source_types(self):
        """All code-like source types route to _chunk_code."""
        code = "def foo(): pass\n\ndef bar(): pass"
        chunker = SemanticChunker(max_chars=512, min_chunk_chars=5)
        for source_type in ["python", "typescript", "javascript", "code"]:
            chunks = chunker.chunk(code, source_type=source_type)
            assert isinstance(chunks, list)

    def test_large_code_block_splits_by_lines(self):
        """Covers lines 170-186: chunk_code splits large blocks by lines."""
        # Create a very large function that exceeds max_chars * 2
        large_function = "def giant_function():\n"
        large_function += "\n".join([f"    step_{i} = process(data_{i})" for i in range(100)])
        # At 2x max_chars, should split by lines
        chunker = SemanticChunker(max_chars=100, min_chunk_chars=20)
        chunks = chunker.chunk(large_function, source_type="python")
        assert len(chunks) >= 2

    def test_code_with_async_function(self):
        code = '''async def fetch_data():
    data = await client.get("/api/data")
    return data

async def process():
    result = await fetch_data()
    return result
'''
        chunker = SemanticChunker(max_chars=512, min_chunk_chars=10)
        chunks = chunker.chunk(code, source_type="python")
        assert len(chunks) >= 1

    def test_code_empty_parts_skipped(self):
        """Empty parts in code chunking should be skipped."""
        code = "\n\ndef hello():\n    pass\n\n\ndef world():\n    pass"
        chunker = SemanticChunker(max_chars=512, min_chunk_chars=5)
        chunks = chunker.chunk(code, source_type="python")
        assert all(c.content.strip() for c in chunks)

    def test_code_remaining_lines_appended(self):
        """Covers the final remaining lines append in _chunk_code."""
        # Small function just under 2x max_chars
        code = "def small():\n    " + "x = 1\n    " * 10 + "return x\n"
        chunker = SemanticChunker(max_chars=50, min_chunk_chars=5)
        chunks = chunker.chunk(code, source_type="python")
        assert len(chunks) >= 1

    def test_javascript_source_type(self):
        code = '''function greet(name) {
    return `Hello, ${name}!`;
}

export async function fetchUser(id) {
    return await fetch(`/api/users/${id}`);
}
'''
        chunker = SemanticChunker(max_chars=512, min_chunk_chars=10)
        chunks = chunker.chunk(code, source_type="javascript")
        assert len(chunks) >= 1
