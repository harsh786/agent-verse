"""IngestionOrchestrator — routes content to the right parser, chunker, and store."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from app.ingestion.content_classifier import ContentClassifier, ContentType
from app.ingestion.chunking_strategy_selector import ChunkingStrategySelector
from app.ingestion.parser_registry import ParserRegistry

if TYPE_CHECKING:
    from app.tenancy.context import TenantContext


@dataclass
class IngestionResult:
    ingestion_id: str
    tenant_id: str
    collection_id: str
    content_type: ContentType
    chunking_strategy: str
    chunks_created: int
    source_url: str = ""
    chunk_ids: list[str] = field(default_factory=list)


class IngestionOrchestrator:
    def __init__(
        self,
        *,
        knowledge_store: Any = None,
        embedder: Any = None,
    ) -> None:
        self._kb = knowledge_store
        self._embedder = embedder
        self._classifier = ContentClassifier()
        self._chunking_selector = ChunkingStrategySelector()
        self._parser_registry = ParserRegistry()

    async def ingest(
        self,
        content: str,
        *,
        content_type: str = "auto",
        collection_id: str,
        tenant_ctx: "TenantContext",
        source_url: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> IngestionResult:
        # 1. Detect content type
        if content_type in ("auto", "unknown"):
            detected = self._classifier.classify(content)
        else:
            try:
                detected = ContentType(content_type)
            except ValueError:
                detected = ContentType.TEXT

        # 2. Select chunking strategy
        chunking_strategy = self._chunking_selector.select(detected)

        # 3. Parse into chunks
        parser = self._parser_registry.get_parser(detected)
        chunks_text = parser.parse(content)

        # 4. Store chunks (in-memory or real KB)
        chunk_ids: list[str] = []
        for chunk_text in chunks_text:
            chunk_id = uuid.uuid4().hex
            if self._kb is not None:
                try:
                    await self._kb.ingest_document(
                        collection_id=collection_id,
                        content=chunk_text,
                        metadata={
                            **(metadata or {}),
                            "content_type": detected.value,
                            "chunking_strategy": chunking_strategy,
                        },
                        tenant_ctx=tenant_ctx,
                        embedder=self._embedder,
                        source_url=source_url,
                        source_type=detected.value,
                    )
                except Exception:
                    pass
            chunk_ids.append(chunk_id)

        return IngestionResult(
            ingestion_id=uuid.uuid4().hex,
            tenant_id=tenant_ctx.tenant_id,
            collection_id=collection_id,
            content_type=detected,
            chunking_strategy=chunking_strategy,
            chunks_created=len(chunk_ids),
            source_url=source_url,
            chunk_ids=chunk_ids,
        )
