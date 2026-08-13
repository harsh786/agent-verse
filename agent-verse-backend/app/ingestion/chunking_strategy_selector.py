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

# Advanced strategies that can be requested explicitly at the collection level
_ADVANCED_STRATEGIES: frozenset[str] = frozenset({
    "parent_child",
    "sentence_window",
    "fixed",
    "agentic",
    "agentic_chunking",
})


class ChunkingStrategySelector:
    def select(self, content_type: ContentType) -> str:
        """Return default strategy for a content type."""
        return _STRATEGY_MAP.get(content_type, "semantic")

    def select_advanced(
        self,
        content_type: ContentType,
        collection_strategy: str | None = None,
    ) -> str:
        """Return collection-level override if valid, else default for content type."""
        if collection_strategy and collection_strategy in _ADVANCED_STRATEGIES:
            return collection_strategy
        return self.select(content_type)

    @staticmethod
    def is_advanced(strategy: str) -> bool:
        """Return True for strategies that need special orchestrator dispatch."""
        return strategy in _ADVANCED_STRATEGIES
