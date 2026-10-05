"""Token-aware text chunking using tiktoken.

Produces chunks with a guaranteed maximum token count and configurable overlap,
using the cl100k_base encoding (used by GPT-4, text-embedding-3-*, voyage-3).

Falls back to character-based chunking when tiktoken is not installed so the
module is safe to import in environments without the tiktoken package.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ParentWindow",
    "build_parent_windows",
    "chunk_by_chars",
    "chunk_by_tokens",
    "chunk_structured",
    "count_tokens",
]


@dataclass(frozen=True, slots=True)
class ParentWindow:
    """A stable sentence/chunk window independent of embedding dimensions."""

    content: str
    center_index: int
    start_index: int
    end_index: int


def build_parent_windows(chunks: list[str], radius: int = 1) -> list[ParentWindow]:
    """Build one citation window around each source chunk."""
    if radius < 0:
        raise ValueError("radius cannot be negative")
    windows: list[ParentWindow] = []
    for index, content in enumerate(chunks):
        if not content.strip():
            continue
        start = max(0, index - radius)
        end = min(len(chunks), index + radius + 1)
        windows.append(
            ParentWindow(
                content="\n\n".join(chunk.strip() for chunk in chunks[start:end] if chunk.strip()),
                center_index=index,
                start_index=start,
                end_index=end,
            )
        )
    return windows


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


# ---------------------------------------------------------------------------
# Structure-aware chunking (uploads)
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")
_SENTENCE_END = re.compile(r"(?<=[.!?;\u3002\uff01\uff1f\u0964])\s+")


@functools.lru_cache(maxsize=1)
def _encoding() -> Any:
    try:
        import tiktoken
    except ImportError:
        return None
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """cl100k_base tokens of ``text`` (about 4 characters per token without tiktoken)."""
    enc = _encoding()
    if enc is None:
        return max(1, len(text) // 4) if text else 0
    return len(enc.encode(text))


def _token_windows(text: str, max_tokens: int) -> list[str]:
    enc = _encoding()
    if enc is None:
        step = max_tokens * 4
        return [text[i : i + step] for i in range(0, len(text), step)]
    tokens = enc.encode(text)
    return [enc.decode(tokens[i : i + max_tokens]) for i in range(0, len(tokens), max_tokens)]


@dataclass(frozen=True, slots=True)
class _Unit:
    text: str
    offset: int
    tokens: int
    level: int  # markdown heading level, 0 for body text


def _units(text: str, max_tokens: int) -> Iterator[_Unit]:
    """Lines of ``text`` (blank lines dropped); a line over the budget becomes its
    sentences, a sentence over the budget token windows."""
    pos = 0
    for line in text.split("\n"):
        start, pos = pos, pos + len(line) + 1
        if not line.strip():
            continue
        tokens = count_tokens(line)
        heading = _HEADING_RE.match(line)
        level = len(heading.group(1)) if heading and len(line) <= 300 else 0
        if tokens <= max_tokens:
            yield _Unit(line, start, tokens, level)
            continue
        cursor = 0
        for sentence in _SENTENCE_END.split(line):
            if not sentence.strip():
                continue
            at = line.find(sentence, cursor)
            at = cursor if at < 0 else at
            cursor = at + len(sentence)
            pieces = (
                [sentence]
                if count_tokens(sentence) <= max_tokens
                else _token_windows(sentence, max_tokens)
            )
            inner = 0
            for piece in pieces:
                found = sentence.find(piece, inner)
                inner = found if found >= 0 else inner
                yield _Unit(piece, start + at + inner, count_tokens(piece), 0)
                inner += len(piece)


def _cost(units: list[_Unit]) -> int:
    # one token per line break: a conservative bound for the joined text
    return sum(u.tokens for u in units) + max(0, len(units) - 1)


def chunk_structured(
    text: str,
    max_tokens: int = 512,
    overlap_tokens: int = 64,
    heading_break_tokens: int | None = None,
) -> list[tuple[str, int]]:
    """Chunk ``text`` along its structure: ``[(chunk_text, char_offset), ...]``.

    Whole lines are packed up to ``max_tokens`` (a line longer than that is
    split into sentences, a sentence longer than that into token windows), so a
    table row or a sentence is never cut in half. A markdown heading starts a
    new chunk once the current one holds ``heading_break_tokens`` (default a
    quarter of the budget): an edit inside one section then leaves the other
    sections' chunks byte-identical (stable chunk ids, P1a-7) — fixed token
    windows shifted every later chunk. A section split for size carries its
    heading path into the continuation chunk, and consecutive chunks of a
    section overlap by up to ``overlap_tokens`` of whole lines. ``char_offset``
    is where the chunk's first line (after any carried headings) starts.
    """
    if not text.strip():
        return []
    min_break = max_tokens // 4 if heading_break_tokens is None else heading_break_tokens
    chunks: list[tuple[str, int]] = []
    path: list[_Unit] = []  # current heading path (one per level)
    prefix: list[_Unit] = []  # headings carried into a continuation chunk
    body: list[_Unit] = []
    own = 0  # units of ``body`` that are not overlap

    def flush() -> None:
        if own and body:
            chunks.append(("\n".join(u.text for u in prefix + body), body[0].offset))

    for unit in _units(text, max_tokens):
        if unit.level:
            if own and _cost(body[-own:]) >= min_break:
                flush()
                prefix, body, own = [], [], 0
            path = [h for h in path if h.level < unit.level] + [unit]
        elif own and _cost(prefix + body + [unit]) > max_tokens:
            while body and body[-1].level:  # never end a chunk on a heading
                body.pop()
                own -= 1
            flush()
            overlap: list[_Unit] = []
            for prev in reversed(body):
                if prev.level or _cost([prev, *overlap]) > overlap_tokens:
                    break
                overlap.insert(0, prev)
            prefix = list(path)
            body, own = overlap, 0
            while body and _cost(prefix + body + [unit]) > max_tokens:
                body.pop(0)
            if _cost(prefix + body + [unit]) > max_tokens:
                prefix = []
        body.append(unit)
        own += 1
    flush()
    return chunks
