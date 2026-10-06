from __future__ import annotations

from app.ingestion.chunkers.ast_chunker import ASTChunker
from app.ingestion.chunkers.base import Chunk, ChunkerBase
from app.ingestion.chunkers.heading import HeadingChunker
from app.ingestion.chunkers.pdf_layout import PDFLayoutChunker
from app.ingestion.chunkers.region import RegionChunker
from app.ingestion.chunkers.scene import SceneChunker
from app.ingestion.chunkers.semantic import SemanticChunker
from app.ingestion.chunkers.structure import DomChunker, ParagraphChunker
from app.ingestion.chunkers.table import TableChunker
from app.ingestion.chunkers.timestamp import TimestampChunker


class UnsupportedChunkingStrategyError(ValueError):
    """A chunking strategy name with no real implementation behind it."""

    def __init__(self, strategy: str) -> None:
        self.strategy = strategy
        super().__init__(
            f"chunking strategy {strategy!r} is not implemented; "
            f"use one of: {', '.join(sorted(SUPPORTED_CHUNKING_STRATEGIES))}"
        )


class FixedSizeChunker(ChunkerBase):
    """Fixed-size word windows with overlap (no sentence / structure awareness)."""

    def __init__(self, size: int = 400, overlap: int = 50) -> None:
        self._size = size
        self._step = max(1, size - overlap)

    def chunk(self, content: str) -> list[Chunk]:
        words = content.split()
        chunks: list[Chunk] = []
        for index, start in enumerate(range(0, len(words), self._step)):
            window = " ".join(words[start : start + self._size])
            if window.strip():
                chunks.append(Chunk(content=window, chunk_index=index))
            if start + self._size >= len(words):
                break
        return chunks


_STRATEGY_TO_CHUNKER: dict[str, ChunkerBase] = {
    "semantic": SemanticChunker(),
    "heading": HeadingChunker(),
    # a04-F068-02: paragraph / dom used to alias SemanticChunker (blank-line
    # splits only), so a DOCX / HTML document — one block per line — became one
    # "paragraph" cut at sentence boundaries.
    "paragraph": ParagraphChunker(),
    "ast": ASTChunker(),
    "code": ASTChunker(),
    "layout": PDFLayoutChunker(),
    "page": PDFLayoutChunker(),
    "section": PDFLayoutChunker(),
    "dom": DomChunker(),
    "timestamp": TimestampChunker(),
    "scene": SceneChunker(),
    "row_group": TableChunker(),
    "table": TableChunker(),
    "record": TableChunker(),
    # No real spatial/bounding-box chunker exists in this codebase (see
    # app.ingestion.chunkers.region for why); RegionChunker logs a warning and
    # tags chunk metadata so the fallback is observable rather than silent.
    "region": RegionChunker(),
    "fixed": FixedSizeChunker(),
    # parent_child / sentence_window / agentic / agentic_chunking used to alias
    # SemanticChunker here, so a Source configured with them got semantic chunks
    # while reporting the strategy it asked for. They are not implemented on the
    # flat-chunk path (parent/window context is never stored), so they are
    # refused instead. (agentic_chunking remains an ingestion-time *indexing*
    # strategy on POST /knowledge/collections/{id}/documents.)
}

# What a Source's ``chunking_strategy`` may be ("auto" = pick by content type).
SUPPORTED_CHUNKING_STRATEGIES: frozenset[str] = frozenset({"auto", *_STRATEGY_TO_CHUNKER})


def get_chunker_for_strategy(strategy: str) -> ChunkerBase:
    """The real chunker for ``strategy``; raises for a name with no implementation."""
    chunker = _STRATEGY_TO_CHUNKER.get(strategy)
    if chunker is None:
        raise UnsupportedChunkingStrategyError(strategy)
    return chunker


__all__ = [
    "SUPPORTED_CHUNKING_STRATEGIES",
    "ASTChunker",
    "Chunk",
    "ChunkerBase",
    "DomChunker",
    "FixedSizeChunker",
    "HeadingChunker",
    "PDFLayoutChunker",
    "ParagraphChunker",
    "RegionChunker",
    "SceneChunker",
    "SemanticChunker",
    "TableChunker",
    "TimestampChunker",
    "UnsupportedChunkingStrategyError",
    "get_chunker_for_strategy",
]
