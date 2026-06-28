"""Token-aware text chunking using tiktoken.

Produces chunks with a guaranteed maximum token count and configurable overlap,
using the cl100k_base encoding (used by GPT-4, text-embedding-3-*, voyage-3).

Falls back to character-based chunking when tiktoken is not installed so the
module is safe to import in environments without the tiktoken package.
"""
from __future__ import annotations

__all__ = ["chunk_by_chars", "chunk_by_tokens"]


def _chunk_by_chars(text: str, max_chars: int, overlap: int) -> list[str]:
    """Character-based fallback chunker used when tiktoken is unavailable."""
    if not text.strip():
        return []

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        if end >= len(text):
            break
        start += max_chars - overlap

    return chunks


def chunk_by_tokens(
    text: str,
    max_tokens: int = 512,
    overlap_tokens: int = 64,
) -> list[str]:
    """Chunk *text* into token-bounded segments with overlap.

    Uses ``tiktoken`` (cl100k_base encoding) for precise token counting.
    Falls back to ``chunk_by_chars`` (approx 4 chars per token) when
    tiktoken is not installed.

    Args:
        text: Input text to chunk.
        max_tokens: Maximum tokens per chunk (default 512, fits most models).
        overlap_tokens: Token overlap between consecutive chunks (default 64).
            Overlap preserves cross-boundary context for retrieval.

    Returns:
        A list of non-empty text chunks.  The last chunk may have fewer than
        ``max_tokens`` tokens.  Returns ``[]`` for blank input.

    Example::

        chunks = chunk_by_tokens("Lorem ipsum ...", max_tokens=256, overlap_tokens=32)
        assert all(len(c) > 0 for c in chunks)
    """
    if not text.strip():
        return []

    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(text)

        if len(tokens) == 0:
            return []

        chunks: list[str] = []
        start = 0

        while start < len(tokens):
            end = min(start + max_tokens, len(tokens))
            chunk_tokens = tokens[start:end]
            decoded = enc.decode(chunk_tokens)
            if decoded.strip():
                chunks.append(decoded)
            if end >= len(tokens):
                break
            # Advance by (max_tokens - overlap_tokens) so adjacent chunks share
            # overlap_tokens worth of context.
            step = max_tokens - overlap_tokens
            if step <= 0:
                # Guard: if overlap >= max, advance by 1 to prevent infinite loop
                step = 1
            start += step

        return chunks

    except ImportError:
        # tiktoken not installed — fall back to approximate char-based chunking.
        # Rule of thumb: ~4 chars per token for English text.
        return _chunk_by_chars(
            text,
            max_chars=max_tokens * 4,
            overlap=overlap_tokens * 4,
        )


# ---------------------------------------------------------------------------
# Alias kept for backward compat with code that imports chunk_by_chars
# ---------------------------------------------------------------------------
chunk_by_chars = _chunk_by_chars
