from __future__ import annotations

from app.ingestion.chunkers.ast_chunker import ASTChunker
from app.ingestion.chunkers.base import Chunk, ChunkerBase
from app.ingestion.chunkers.heading import HeadingChunker
from app.ingestion.chunkers.pdf_layout import PDFLayoutChunker
from app.ingestion.chunkers.region import RegionChunker
from app.ingestion.chunkers.scene import SceneChunker
from app.ingestion.chunkers.semantic import SemanticChunker
from app.ingestion.chunkers.table import TableChunker
from app.ingestion.chunkers.timestamp import TimestampChunker

_STRATEGY_TO_CHUNKER: dict[str, ChunkerBase] = {
    "semantic": SemanticChunker(),
    "heading": HeadingChunker(),
    "paragraph": SemanticChunker(),
    "ast": ASTChunker(),
    "code": ASTChunker(),
    "layout": PDFLayoutChunker(),
    "page": PDFLayoutChunker(),
    "section": PDFLayoutChunker(),
    "dom": SemanticChunker(),
    "timestamp": TimestampChunker(),
    "scene": SceneChunker(),
    "row_group": TableChunker(),
    "table": TableChunker(),
    "record": TableChunker(),
    # No real spatial/bounding-box chunker exists in this codebase (see
    # app.ingestion.chunkers.region for why); RegionChunker logs a warning and
    # tags chunk metadata so the fallback is observable rather than silent.
    "region": RegionChunker(),
    # Advanced strategies — fall back to SemanticChunker for the flat-chunk pass;
    # the orchestrator handles the real parent_child / sentence_window dispatch.
    "parent_child": SemanticChunker(),
    "sentence_window": SemanticChunker(),
    "fixed": SemanticChunker(),
    "agentic": SemanticChunker(),
    "agentic_chunking": SemanticChunker(),
}


def get_chunker_for_strategy(strategy: str) -> ChunkerBase:
    return _STRATEGY_TO_CHUNKER.get(strategy, SemanticChunker())


__all__ = [
    "ASTChunker",
    "Chunk",
    "ChunkerBase",
    "HeadingChunker",
    "PDFLayoutChunker",
    "RegionChunker",
    "SceneChunker",
    "SemanticChunker",
    "TableChunker",
    "TimestampChunker",
    "get_chunker_for_strategy",
]
