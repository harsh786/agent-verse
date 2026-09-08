"""IngestionOrchestrator — routes content to the right parser, chunker, and store."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.ingestion.chunking_strategy_selector import ChunkingStrategySelector
from app.ingestion.content_classifier import ContentClassifier, ContentType
from app.ingestion.parser_registry import ParserRegistry
from app.rag.models import Chunk

if TYPE_CHECKING:
    from app.rag.contracts import RAGStrategy
    from app.rag.indexing import IndexingDependency, RAGIndexingConfig
    from app.tenancy.context import TenantContext


def _content_hash(text: str) -> str:
    """SHA-256 of the UTF-8 content for chunk-level deduplication."""
    return hashlib.sha256(text.encode()).hexdigest()


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
    chunks_prepared: int = 0
    persisted: bool = False


class EmptyIndexedContentError(ValueError):
    """Raised when indexed ingestion has no nonblank chunks to replace a source."""


class IngestionOrchestrator:
    def __init__(
        self,
        *,
        knowledge_store: Any = None,
        embedder: Any = None,
        indexing_dependencies: Mapping[RAGStrategy, IndexingDependency] | None = None,
        rag_indexing_config: RAGIndexingConfig | None = None,
    ) -> None:
        self._kb = knowledge_store
        self._embedder = embedder
        self._indexing_dependencies = dict(indexing_dependencies or {})
        self._rag_indexing_config = rag_indexing_config
        self._classifier = ContentClassifier()
        self._chunking_selector = ChunkingStrategySelector()
        self._parser_registry = ParserRegistry()
        self._embedding_orchestrator: Any = None

    def _embedding_orch(self) -> Any:
        """Lazily build and cache the EmbeddingOrchestrator (avoids import cycles)."""
        if self._embedding_orchestrator is None:
            from app.embedding.orchestrator import EmbeddingOrchestrator

            self._embedding_orchestrator = EmbeddingOrchestrator()
        return self._embedding_orchestrator

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

    def _chunk(self, content: str, ct: Any, strategy_override: str | None = None) -> list[str]:
        """Dispatch to appropriate chunker. Supports advanced strategies.

        If *strategy_override* is an advanced strategy (parent_child / sentence_window /
        fixed) the orchestrator calls the dedicated implementation and flattens the
        results to a plain list of strings so downstream code is unaffected.
        """
        effective_strategy = strategy_override or (
            self._chunking_selector.select(ct) if ct is not None else "semantic"
        )

        # --- parent_child: produce child-chunk texts (parents stored separately) ---
        if effective_strategy == "parent_child":
            try:
                from app.rag.parent_child_chunker import ParentChildChunker

                pc_chunker = ParentChildChunker()
                parent_chunks = pc_chunker.chunk(content)
                texts: list[str] = [
                    cc.content for pc in parent_chunks for cc in pc.children if cc.content.strip()
                ]
                if texts:
                    return texts
            except Exception:
                pass  # fall through to semantic

        # --- sentence_window: use window chunks directly ---
        if effective_strategy == "sentence_window":
            try:
                from app.rag.sentence_window import SentenceWindowChunker

                sw = SentenceWindowChunker()
                sw_chunks = sw.chunk(content)
                texts = [c.content for c in sw_chunks if c.content.strip()]
                if texts:
                    return texts
            except Exception:
                pass  # fall through to semantic

        # --- fixed: fixed-size chunks via RAG SemanticChunker with 'fixed' strategy ---
        if effective_strategy == "fixed":
            try:
                from app.rag.chunker import SemanticChunker as RagChunker

                chunker_fixed = RagChunker(strategy="fixed")
                texts = [c.content for c in chunker_fixed.chunk(content) if c.content.strip()]
                if texts:
                    return texts
            except Exception:
                pass

        # --- Standard dispatch via chunker registry ---
        try:
            from app.ingestion.chunkers import get_chunker_for_strategy

            chunker = get_chunker_for_strategy(effective_strategy)
            if chunker is not None:
                chunks = chunker.chunk(content)
                result = [
                    c.content if hasattr(c, "content") else str(c)
                    for c in chunks
                    if (c.content if hasattr(c, "content") else str(c)).strip()
                ]
                if result:
                    return result
        except Exception as _chunk_exc:
            try:
                from app.observability.logging import get_logger

                get_logger(__name__).warning(
                    "ingestion_chunker_dispatch_failed",
                    error=str(_chunk_exc)[:80],
                )
            except Exception:
                pass
        # Fallback: paragraph split
        if ct is not None:
            from app.ingestion.content_classifier import ContentType

            if ct == ContentType.CODE:
                import re

                blocks = re.split(r"(?m)^(?=def |class |function |const |let )", content)
                return [b.strip() for b in blocks if b.strip()] or [content]
            if ct in (ContentType.HTML, ContentType.WEB_PAGE):
                import re

                text = re.sub(r"<[^>]+>", " ", content).strip()
                return [text] if text else [content]
        paras = [p.strip() for p in content.split("\n\n") if p.strip()]
        return paras or [content]

    def _chunk_with_quality_check(self, content: str, ct: Any) -> list[str]:
        """Chunk content and filter out low-quality chunks (public API for tests)."""
        raw = self._chunk(content, ct)
        try:
            from app.ingestion.quality_checks import QualityChecker

            checker = QualityChecker(min_length=20)
            filtered = [c for c in raw if checker.check(c).passed]
            return filtered if filtered else raw
        except Exception:
            return raw

    async def ingest(
        self,
        content: str,
        *,
        content_type: str = "auto",
        collection_id: str,
        tenant_ctx: TenantContext,
        source_url: str = "",
        metadata: dict[str, Any] | None = None,
        dry_run: bool = False,
        in_memory_only: bool = False,
        source_identity: str = "",
    ) -> IngestionResult:
        # 1. Detect content type
        if content_type in ("auto", "unknown"):
            detected = self._classifier.classify(content)
        else:
            try:
                detected = ContentType(content_type)
            except ValueError:
                detected = ContentType.TEXT

        # 1b. Select embedding model policy for this content type.
        # D-10: the selection is no longer cosmetic — it is ACTUALLY used to route
        # the physical embedding below (see embed_for_content). Record it in
        # metadata for provenance and downstream re-embedding decisions.
        try:
            _emb_selection = self._embedding_orch().select(
                content_type=detected, tenant_ctx=tenant_ctx
            )
            if metadata is None:
                metadata = {}
            metadata["embedding_model"] = _emb_selection.model_id
            # D-11: image/video content is embedded as caption-then-text-embed, not
            # a native multimodal vector — record that honestly.
            metadata["embedding_input"] = (
                "text_of_caption" if _emb_selection.requires_captioning else "native"
            )
        except Exception:
            pass

        # 2. Select chunking strategy
        chunking_strategy = self._chunking_selector.select(detected)

        # 3. Parse into chunks (with quality filtering)
        parser = self._parser_registry.get_parser(detected)
        raw_texts = parser.parse(content)
        chunks_text = self._filter_quality(raw_texts)

        chunks_prepared = len(chunks_text)
        if (
            self._rag_indexing_config is not None
            and self._rag_indexing_config.strategies
            and not any(chunk.strip() for chunk in chunks_text)
        ):
            raise EmptyIndexedContentError("Indexed content produced no indexable chunks")
        if dry_run:
            return IngestionResult(
                ingestion_id=uuid.uuid4().hex,
                tenant_id=tenant_ctx.tenant_id,
                collection_id=collection_id,
                content_type=detected,
                chunking_strategy=chunking_strategy,
                chunks_created=0,
                source_url=source_url,
                chunk_ids=[],
                chunks_prepared=chunks_prepared,
                persisted=False,
            )
        if self._kb is None:
            raise RuntimeError("A knowledge store is required unless dry_run=True")

        store_is_in_memory = getattr(self._kb, "_db", None) is None
        if store_is_in_memory and not in_memory_only:
            raise RuntimeError("An in-memory knowledge store requires in_memory_only=True")

        document_id = uuid.uuid4().hex
        if self._rag_indexing_config is not None and self._rag_indexing_config.strategies:
            if in_memory_only:
                raise ValueError("Indexed ingestion does not support in_memory_only")
            if not source_identity.strip():
                raise ValueError("Indexed ingestion requires source_identity")
            if self._embedder is None:
                raise RuntimeError("Configured RAG indexing requires LLM and embedding providers")
            from app.rag.indexing import RAGIndexingPipeline

            identity_scope = "\x1f".join(
                (tenant_ctx.tenant_id, collection_id, source_identity.strip())
            )
            document_id = hashlib.sha256(identity_scope.encode()).hexdigest()

            pipeline = RAGIndexingPipeline(
                store=self._kb,
                embedder=self._embedder,
                dependencies=self._indexing_dependencies,
                config=self._rag_indexing_config,
            )
            indexed_records = await pipeline.index_document(
                collection_id=collection_id,
                document_id=document_id,
                chunks=chunks_text,
                tenant_ctx=tenant_ctx,
                metadata={
                    **(metadata or {}),
                    "content_type": detected.value,
                    "chunking_strategy": chunking_strategy,
                    "source_url": source_url,
                    "source_identity": source_identity,
                    "source_type": detected.value,
                },
            )
            indexed_ids = [record.chunk_id for record in indexed_records]
            return IngestionResult(
                ingestion_id=uuid.uuid4().hex,
                tenant_id=tenant_ctx.tenant_id,
                collection_id=collection_id,
                content_type=detected,
                chunking_strategy=chunking_strategy,
                chunks_created=len(indexed_ids),
                source_url=source_url,
                chunk_ids=indexed_ids,
                chunks_prepared=chunks_prepared,
                persisted=True,
            )

        # D-10: route the embedding through the SELECTED model instead of calling a
        # fixed embedder and discarding the selection. embed_for_content threads the
        # chosen model id into the physical embed call, falling back safely to the
        # configured embedder as the provider.
        routed = await self._embedding_orch().embed_for_content(
            chunks_text,
            content_type=detected,
            tenant_ctx=tenant_ctx,
            default_provider=self._embedder,
        )
        embeddings = routed.embeddings
        if len(embeddings) != chunks_prepared:
            raise RuntimeError("Embedding provider returned an incomplete batch")

        chunks = [
            Chunk(
                document_id=document_id,
                content=chunk_text,
                embedding=embeddings[index],
                chunk_index=index,
                metadata={
                    **(metadata or {}),
                    "content_type": detected.value,
                    "chunking_strategy": chunking_strategy,
                    "source_url": source_url,
                    "source_type": detected.value,
                },
            )
            for index, chunk_text in enumerate(chunks_text)
        ]
        stored_ids = await self._kb.ingest_chunks_async(
            chunks,
            collection_id=collection_id,
            tenant_ctx=tenant_ctx,
        )
        expected_ids = [chunk.chunk_id for chunk in chunks]
        if list(stored_ids) != expected_ids:
            raise RuntimeError("Knowledge store did not commit every prepared chunk")

        if in_memory_only:
            return IngestionResult(
                ingestion_id=uuid.uuid4().hex,
                tenant_id=tenant_ctx.tenant_id,
                collection_id=collection_id,
                content_type=detected,
                chunking_strategy=chunking_strategy,
                chunks_created=0,
                source_url=source_url,
                chunk_ids=[],
                chunks_prepared=chunks_prepared,
                persisted=False,
            )

        return IngestionResult(
            ingestion_id=uuid.uuid4().hex,
            tenant_id=tenant_ctx.tenant_id,
            collection_id=collection_id,
            content_type=detected,
            chunking_strategy=chunking_strategy,
            chunks_created=len(stored_ids),
            source_url=source_url,
            chunk_ids=list(stored_ids),
            chunks_prepared=chunks_prepared,
            persisted=True,
        )
