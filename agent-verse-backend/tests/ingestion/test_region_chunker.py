"""RegionChunker — proves the "region" strategy is an honest, observable
fallback rather than a silent mis-chunk.

Closes an audit gap: `get_chunker_for_strategy("region")` used to return a
bare `SemanticChunker()` with no indication anywhere that "region" (the
default strategy for `ContentType.IMAGE`) has no real spatial/bounding-box
implementation. `RegionChunker` now logs a warning and tags chunk metadata
on every call so the fallback is discoverable, while still producing usable
text chunks (delegating to `SemanticChunker`) so the image ingestion path
that depends on "region" as its default strategy keeps working.
"""

from __future__ import annotations

import structlog

from app.ingestion.chunkers import RegionChunker, get_chunker_for_strategy
from app.ingestion.chunkers.base import Chunk
from app.ingestion.chunkers.region import RegionChunker as RegionChunkerDirect
from app.ingestion.chunkers.semantic import SemanticChunker


def test_get_chunker_for_strategy_region_returns_region_chunker() -> None:
    chunker = get_chunker_for_strategy("region")
    assert isinstance(chunker, RegionChunker)


def test_region_chunker_is_a_chunker_base() -> None:
    from app.ingestion.chunkers.base import ChunkerBase

    assert isinstance(RegionChunkerDirect(), ChunkerBase)


def test_region_chunker_still_produces_usable_text_chunks() -> None:
    """The fallback must not regress the image-ingestion pipeline: image OCR
    text must still be chunked into something usable, just honestly labeled."""
    chunker = RegionChunker()
    text = "First region of extracted text.\n\nSecond region of extracted text."
    chunks = chunker.chunk(text)
    assert len(chunks) >= 1
    assert all(isinstance(c, Chunk) for c in chunks)
    assert all(c.content.strip() for c in chunks)


def test_region_chunker_matches_semantic_chunker_content() -> None:
    """It delegates to SemanticChunker for the actual text split."""
    text = "Para one is here.\n\nPara two is here.\n\nPara three is here."
    region_chunks = RegionChunker().chunk(text)
    semantic_chunks = SemanticChunker().chunk(text)
    assert [c.content for c in region_chunks] == [c.content for c in semantic_chunks]


def test_region_chunker_tags_metadata_as_a_fallback() -> None:
    """Callers/telemetry must be able to detect the fallback programmatically,
    not just by reading logs."""
    chunks = RegionChunker().chunk("Some extracted image text that needs chunking.")
    assert chunks
    for c in chunks:
        assert c.metadata["chunking_strategy_requested"] == "region"
        assert c.metadata["chunking_strategy_fallback"] == "semantic"


def test_region_chunker_logs_a_warning_every_call() -> None:
    """The gap must show up in logs/telemetry rather than being silent."""
    events: list[dict] = []

    def _capture(_logger, _method, event_dict):
        events.append(dict(event_dict))
        return event_dict

    original_processors = structlog.get_config()["processors"]
    structlog.configure(processors=[_capture, lambda _l, _m, ed: str(ed)])
    try:
        RegionChunker().chunk("Image text to chunk.")
    finally:
        structlog.configure(processors=original_processors)

    warnings = [e for e in events if e.get("event") == "region_chunking_not_implemented"]
    assert len(warnings) == 1
    assert "region" in warnings[0]["detail"].lower()


def test_region_chunker_empty_content_does_not_crash() -> None:
    chunks = RegionChunker().chunk("")
    assert isinstance(chunks, list)
