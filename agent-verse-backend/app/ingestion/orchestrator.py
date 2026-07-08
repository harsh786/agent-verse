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

    def _filter_quality(self, chunks: list[str]) -> list[str]:
        """Filter out low-quality chunks using QualityChecker."""
        try:
            from app.ingestion.quality_checks import QualityChecker
            checker = QualityChecker(min_length=20)
            filtered = [c for c in chunks if checker.check(c).passed]
            # Always return at least something if all chunks fail quality
            return filtered if filtered else chunks
        except Exception:
            return chunks

    def _chunk_with_quality_check(self, content: str, ct: Any) -> list[str]:
        """Chunk content and filter out low-quality chunks (public API for tests)."""
        if ct is not None:
            try:
                from app.ingestion.chunking_strategy_selector import ChunkingStrategySelector
                from app.ingestion.content_classifier import ContentType
                ct_enum = ct if isinstance(ct, ContentType) else ContentType.TEXT
                chunking_strategy = self._chunking_selector.select(ct_enum)
                parser = self._parser_registry.get_parser(ct_enum)
                raw_chunks = parser.parse(content)
            except Exception:
                raw_chunks = [content]
        else:
            raw_chunks = [content] if content.strip() else []
        return self._filter_quality(raw_chunks)

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

        # 1b. Select embedding model policy for this content type
        try:
            from app.embedding.orchestrator import EmbeddingOrchestrator
            _emb_orch = EmbeddingOrchestrator()
            _emb_policy = _emb_orch.select(content_type=detected, tenant_ctx=tenant_ctx)
            # Store selected policy in metadata for downstream use
            if metadata is None:
                metadata = {}
            metadata["embedding_model"] = _emb_policy.model_id if _emb_policy else "default"
        except Exception:
            pass

        # 2. Select chunking strategy
        chunking_strategy = self._chunking_selector.select(detected)

        # 3. Parse into chunks (with quality filtering)
        parser = self._parser_registry.get_parser(detected)
        raw_texts = parser.parse(content)
        chunks_text = self._filter_quality(raw_texts)

        # 4. Store chunks (in-memory or real KB)
        chunk_ids: list[str] = []
        for chunk_text in chunks_text:
            if self._kb is not None:
                try:
                    # Let the store generate the canonical chunk_id
                    stored_id = await self._kb.ingest_document(
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
                    # Use store-generated ID if returned (str), else generate local one
                    chunk_ids.append(str(stored_id) if stored_id else uuid.uuid4().hex)
                except Exception:
                    chunk_ids.append(uuid.uuid4().hex)
            else:
                chunk_ids.append(uuid.uuid4().hex)

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
