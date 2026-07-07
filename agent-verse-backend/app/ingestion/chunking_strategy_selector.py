"""ChunkingStrategySelector — selects chunking strategy by content type."""
from __future__ import annotations

from app.ingestion.content_classifier import ContentType

_STRATEGY_MAP: dict[ContentType, str] = {
    ContentType.TEXT: "semantic",
    ContentType.MARKDOWN: "heading",
    ContentType.PDF: "layout",
    ContentType.DOCX: "paragraph",
    ContentType.HTML: "dom",
    ContentType.CODE: "ast",
    ContentType.IMAGE: "region",
    ContentType.AUDIO: "timestamp",
    ContentType.VIDEO: "scene",
    ContentType.CSV: "row_group",
    ContentType.JSON: "record",
    ContentType.WEB_PAGE: "dom",
    ContentType.MIXED: "semantic",
}


class ChunkingStrategySelector:
    def select(self, content_type: ContentType) -> str:
        return _STRATEGY_MAP.get(content_type, "semantic")
