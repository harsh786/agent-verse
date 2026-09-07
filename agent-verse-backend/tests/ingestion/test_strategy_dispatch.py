"""P0-8 (ING-5/6): advertised chunk strategies must reach their real chunker, not fixed."""

from __future__ import annotations

from unittest.mock import patch

from app.ingestion.chunkers import get_chunker_for_strategy
from app.ingestion.chunking_strategy_selector import ChunkingStrategySelector
from app.ingestion.content_classifier import ContentType


def test_pdf_layout_strategy_invokes_pdf_layout_chunker():
    """FAILS TODAY: 'layout' absent from the private chunker_map -> _fixed_chunk."""
    sel = ChunkingStrategySelector()
    with patch("app.ingestion.chunkers.PDFLayoutChunker.chunk", return_value=[]) as m:
        sel.select_and_chunk("some pdf text", ContentType.PDF)
        assert m.called, "PDF default strategy 'layout' must call PDFLayoutChunker"


def test_csv_row_group_invokes_table_chunker():
    """FAILS TODAY: 'row_group' absent from the private chunker_map -> _fixed_chunk."""
    sel = ChunkingStrategySelector()
    with patch("app.ingestion.chunkers.TableChunker.chunk", return_value=[]) as m:
        sel.select_and_chunk("name,role\nAlice,Eng\nBob,Design", ContentType.CSV)
        assert m.called, "CSV default strategy 'row_group' must call TableChunker"


def test_no_advertised_strategy_is_unresolved():
    """Every advertised default strategy must resolve to a concrete chunker."""
    sel = ChunkingStrategySelector()
    for ct in (
        ContentType.PDF,
        ContentType.DOCX,
        ContentType.HTML,
        ContentType.IMAGE,
        ContentType.CSV,
        ContentType.JSON,
    ):
        assert get_chunker_for_strategy(sel.select(ct)) is not None
