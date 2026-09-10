"""IngestionOrchestrator — routes content to the right parser, chunker, and store."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping
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
        embed_provider_resolver: Callable[[str], Any] | None = None,
    ) -> None:
        self._kb = knowledge_store
        self._embedder = embedder
        # Maps an embedding provider name (e.g. "voyage", "openai") to a concrete
        # provider instance so EmbeddingOrchestrator.select's chosen model is
        # embedded on its own provider (multi-model routing / D-10). None → the
        # single configured embedder is used for every content type.
        self._embed_provider_resolver = embed_provider_resolver
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

                chunker_fixed = RagChunker()
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
        # the physical embedding below (see embed_for_content, and the indexed-path
        # wiring further down). Record it in metadata for provenance and downstream
        # re-embedding decisions. `_emb_selection` stays None (never raises) when
        # selection fails, so every consumer below must degrade to the default
        # embedder rather than assume it succeeded.
        _emb_selection: Any = None
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
            _emb_selection = None

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

            # D-10 (indexed-path discard-site): the selection computed above was
            # previously written to metadata and then discarded — the pipeline
            # always embedded on the one fixed `self._embedder` with no model id
            # threaded through at all. Resolve the SAME selection used by the
            # plain path (`embed_for_content`) onto a real provider here too,
            # degrading to the default embedder (never raising) when the
            # selection failed, its provider isn't configured, or its dimension
            # would corrupt the collection's existing vectors.
            effective_embedder = self._embedder
            effective_model = ""
            embedding_model_effective = "default"
            if _emb_selection is not None:
                existing_dim: int | None = None
                dim_lookup = getattr(self._kb, "get_collection_embedding_dim", None)
                if dim_lookup is not None:
                    try:
                        existing_dim = await dim_lookup(collection_id, tenant_ctx=tenant_ctx)
                    except Exception:
                        existing_dim = None
                if existing_dim is not None and existing_dim != _emb_selection.dimension:
                    # Dimension safety: never write a mismatched-dimension vector.
                    # Reuses the collection's stored embedding_dim (the same
                    # source of truth `_persist_chunks` enforces) instead of a
                    # separate check; falls back to the default embedder with no
                    # model override — its own, already-matching, dimension.
                    pass
                else:
                    resolved = None
                    if self._embed_provider_resolver is not None:
                        try:
                            resolved = self._embed_provider_resolver(_emb_selection.provider)
                        except Exception:
                            resolved = None
                    effective_embedder = resolved if resolved is not None else self._embedder
                    effective_model = _emb_selection.model_id
                    embedding_model_effective = _emb_selection.model_id

            if metadata is None:
                metadata = {}
            metadata["embedding_model_effective"] = embedding_model_effective

            pipeline = RAGIndexingPipeline(
                store=self._kb,
                embedder=effective_embedder,
                embed_model=effective_model,
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
            provider_resolver=self._embed_provider_resolver,
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
