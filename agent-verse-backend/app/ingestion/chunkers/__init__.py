from __future__ import annotations
from app.ingestion.chunkers.base import Chunk, ChunkerBase
from app.ingestion.chunkers.semantic import SemanticChunker
from app.ingestion.chunkers.ast_chunker import ASTChunker
from app.ingestion.chunkers.pdf_layout import PDFLayoutChunker
from app.ingestion.chunkers.heading import HeadingChunker
from app.ingestion.chunkers.timestamp import TimestampChunker
from app.ingestion.chunkers.scene import SceneChunker
from app.ingestion.chunkers.table import TableChunker

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
    "region": SemanticChunker(),
}


def get_chunker_for_strategy(strategy: str) -> ChunkerBase:
    return _STRATEGY_TO_CHUNKER.get(strategy, SemanticChunker())


__all__ = ["Chunk", "ChunkerBase", "SemanticChunker", "ASTChunker", "PDFLayoutChunker",
           "HeadingChunker", "TimestampChunker", "SceneChunker", "TableChunker",
           "get_chunker_for_strategy"]
