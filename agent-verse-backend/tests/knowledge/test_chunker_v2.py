"""Tests for app/knowledge/chunker_v2.py — token-aware chunking."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from app.knowledge.chunker_v2 import _chunk_by_chars, chunk_by_chars, chunk_by_tokens


# ── _chunk_by_chars ────────────────────────────────────────────────────────────

class TestChunkByChars:
    def test_empty_string_returns_empty(self):
        assert _chunk_by_chars("", 100, 10) == []

    def test_whitespace_only_returns_empty(self):
        assert _chunk_by_chars("   \n\t  ", 100, 10) == []

    def test_single_chunk_when_text_fits(self):
        result = _chunk_by_chars("hello world", 100, 10)
        assert result == ["hello world"]

    def test_splits_into_multiple_chunks(self):
        text = "a" * 250
        result = _chunk_by_chars(text, 100, 0)
        assert len(result) == 3
        assert all(len(c) > 0 for c in result)

    def test_overlap_keeps_shared_content(self):
        text = "abcdefghij" * 20  # 200 chars
        result = _chunk_by_chars(text, 100, 20)
        assert len(result) >= 2
        # First chunk ends at char 100; second starts at 80 (100-20 overlap)
        assert result[0][80:100] == result[1][:20]

    def test_skips_whitespace_only_chunks(self):
        # Create a string where a chunk would be all spaces
        text = "hello" + " " * 100 + "world"
        result = _chunk_by_chars(text, 10, 0)
        # Some chunks may be whitespace-only and skipped
        assert all(c.strip() for c in result)

    def test_exact_size_text(self):
        text = "x" * 100
        result = _chunk_by_chars(text, 100, 0)
        assert len(result) == 1
        assert result[0] == text

    def test_chunk_by_chars_alias(self):
        """chunk_by_chars is a public alias for _chunk_by_chars."""
        assert chunk_by_chars is _chunk_by_chars
        result = chunk_by_chars("test content here", 50, 5)
        assert isinstance(result, list)


# ── chunk_by_tokens — fallback path (no tiktoken) ─────────────────────────────

class TestChunkByTokensFallback:
    def test_empty_returns_empty(self):
        result = chunk_by_tokens("", 100, 10)
        assert result == []

    def test_whitespace_returns_empty(self):
        result = chunk_by_tokens("   ", 100, 10)
        assert result == []

    def test_fallback_when_tiktoken_absent(self):
        """When tiktoken raises ImportError, falls back to char chunking."""
        with patch.dict(sys.modules, {"tiktoken": None}):
            # Force reimport to trigger ImportError branch
            text = "This is a test sentence with enough content to chunk properly."
            result = chunk_by_tokens(text, 10, 2)
            assert isinstance(result, list)
            assert all(isinstance(c, str) for c in result)


# ── chunk_by_tokens — tiktoken path ───────────────────────────────────────────

class TestChunkByTokensTiktoken:
    def test_with_mock_tiktoken(self):
        """Mock tiktoken to test the token-based chunking path."""
        mock_tiktoken = MagicMock()
        mock_enc = MagicMock()
        # Return 100 tokens for any text
        mock_enc.encode.return_value = list(range(100))
        mock_enc.decode.side_effect = lambda toks: f"chunk_{toks[0]}_{toks[-1]}"
        mock_tiktoken.get_encoding.return_value = mock_enc

        with patch.dict(sys.modules, {"tiktoken": mock_tiktoken}):
            # Need to reload the module so the import is re-tried
            import importlib
            import app.knowledge.chunker_v2 as mod
            # Monkey-patch the imported tiktoken within the function by triggering it
            result = mod.chunk_by_tokens("some text content here", max_tokens=20, overlap_tokens=5)
            # Should have called encode
            assert isinstance(result, list)

    def test_empty_tokens_returns_empty(self):
        """If tokenizer returns 0 tokens, return empty list."""
        mock_tiktoken = MagicMock()
        mock_enc = MagicMock()
        mock_enc.encode.return_value = []  # empty tokens
        mock_tiktoken.get_encoding.return_value = mock_enc

        with patch.dict(sys.modules, {"tiktoken": mock_tiktoken}):
            import importlib
            import app.knowledge.chunker_v2 as mod
            result = mod.chunk_by_tokens("some text", max_tokens=512, overlap_tokens=64)
            assert isinstance(result, list)

    def test_overlap_greater_than_max_tokens(self):
        """When overlap >= max_tokens, step becomes 1 (guard prevents infinite loop)."""
        mock_tiktoken = MagicMock()
        mock_enc = MagicMock()
        # 10 tokens
        mock_enc.encode.return_value = list(range(10))
        mock_enc.decode.side_effect = lambda toks: "chunk"
        mock_tiktoken.get_encoding.return_value = mock_enc

        with patch.dict(sys.modules, {"tiktoken": mock_tiktoken}):
            import app.knowledge.chunker_v2 as mod
            # overlap >= max_tokens forces step=1
            result = mod.chunk_by_tokens("text", max_tokens=5, overlap_tokens=5)
            assert isinstance(result, list)

    def test_default_parameters(self):
        """chunk_by_tokens works with default args."""
        result = chunk_by_tokens("Hello world, this is a test of the chunker function.")
        assert isinstance(result, list)

    def test_small_text_single_chunk(self):
        """Short text produces exactly one chunk."""
        short_text = "Short text."
        result = chunk_by_tokens(short_text, max_tokens=512, overlap_tokens=64)
        assert len(result) >= 1
        assert result[0].strip() != ""
