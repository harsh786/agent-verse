"""RegionChunker — honest fallback for the unimplemented "region" strategy.

`ChunkingStrategySelector` maps `ContentType.IMAGE` to the "region" strategy
by default (see `app.ingestion.chunking_strategy_selector`), meaning every
image ingestion goes through here unless a caller explicitly overrides the
strategy. There is no real spatial/bounding-box chunker anywhere in this
codebase: `app.multimodal.models.ExtractedSpan.bounding_box` is defined but
never populated or read by anything, and no OCR/vision span-grouping exists
that could back true region-based chunking (see `app/ocr/` and
`app/multimodal/`).

Building real CV region detection is out of scope here. Instead of silently
chunking image-derived text as if "region" were a real spatial strategy (the
previous behavior — a bare alias to `SemanticChunker`), this chunker makes
the gap observable:
  * it logs a structured warning every call, so the fallback shows up in
    logs/telemetry instead of hiding behind a plausible strategy name, and
  * it tags every returned chunk's metadata with
    ``chunking_strategy_requested="region"`` and
    ``chunking_strategy_fallback="semantic"`` so callers/tests can detect the
    fallback programmatically.
"""

from __future__ import annotations

from app.ingestion.chunkers.base import Chunk, ChunkerBase
from app.ingestion.chunkers.semantic import SemanticChunker
from app.observability.logging import get_logger

logger = get_logger(__name__)


class RegionChunker(ChunkerBase):
    """Falls back to text-based semantic chunking with an explicit, logged
    warning — real spatial/region-aware image chunking is not implemented."""

    def __init__(self) -> None:
        self._delegate = SemanticChunker()

    def chunk(self, content: str) -> list[Chunk]:
        logger.warning(
            "region_chunking_not_implemented",
            detail=(
                "'region' chunking strategy has no real spatial/bounding-box "
                "implementation; falling back to text-based SemanticChunker. "
                "Chunk boundaries are NOT aligned to visual regions."
            ),
        )
        chunks = self._delegate.chunk(content)
        for c in chunks:
            c.metadata.setdefault("chunking_strategy_requested", "region")
            c.metadata.setdefault("chunking_strategy_fallback", "semantic")
        return chunks
