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

    def select_and_chunk(
        self,
        text: str,
        content_type: Any,
        strategy_override: str | None = None,
    ) -> list[str]:
        """Select strategy and chunk text. Returns list of non-empty text chunks.

        Used by IngestionPipeline Stage 8.
        """
        from app.ingestion.content_classifier import ContentType as CT
        ct = content_type if isinstance(content_type, CT) else CT.TEXT
        strategy = strategy_override or self.select(ct)

        # Dispatch to the appropriate chunker
        chunker_map = {
            "semantic": "_semantic_chunk",
            "heading": "_heading_chunk",
            "ast": "_ast_chunk",
            "table": "_table_chunk",
            "timestamp": "_timestamp_chunk",
            "scene": "_scene_chunk",
            "fixed": "_fixed_chunk",
        }
        method_name = chunker_map.get(strategy, "_fixed_chunk")
        method = getattr(self, method_name, self._fixed_chunk)
        try:
            result = method(text)
            return [c for c in result if c.strip()]
        except Exception:
            return self._fixed_chunk(text)

    def _fixed_chunk(self, text: str, size: int = 400, overlap: int = 50) -> list[str]:
        words = text.split()
        step = max(1, size - overlap)
        result = []
        for i in range(0, len(words), step):
            chunk = " ".join(words[i: i + size])
            if chunk.strip():
                result.append(chunk)
        return result or [text[:2000]]

    def _semantic_chunk(self, text: str) -> list[str]:
        """Sentence-boundary semantic chunking."""
        try:
            from app.ingestion.chunkers.semantic import SemanticChunker
            chunks = SemanticChunker().chunk(text)
            return [c.content for c in chunks]
        except Exception:
            return self._fixed_chunk(text)

    def _heading_chunk(self, text: str) -> list[str]:
        try:
            from app.ingestion.chunkers.heading import HeadingChunker
            chunks = HeadingChunker().chunk(text)
            return [c.content for c in chunks]
        except Exception:
            return self._fixed_chunk(text)

    def _ast_chunk(self, text: str) -> list[str]:
        try:
            from app.ingestion.chunkers.ast_chunker import ASTChunker
            chunks = ASTChunker().chunk(text)
            return [c.content for c in chunks]
        except Exception:
            return self._fixed_chunk(text)

    def _table_chunk(self, text: str) -> list[str]:
        try:
            from app.ingestion.chunkers.table import TableChunker
            chunks = TableChunker().chunk(text)
            return [c.content for c in chunks]
        except Exception:
            return self._fixed_chunk(text)

    def _timestamp_chunk(self, text: str) -> list[str]:
        try:
            from app.ingestion.chunkers.timestamp import TimestampChunker
            chunks = TimestampChunker().chunk(text)
            return [c.content for c in chunks]
        except Exception:
            return self._fixed_chunk(text)

    def _scene_chunk(self, text: str) -> list[str]:
        try:
            from app.ingestion.chunkers.scene import SceneChunker
            chunks = SceneChunker().chunk(text)
            return [c.content for c in chunks]
        except Exception:
            return self._fixed_chunk(text)
