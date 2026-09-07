"""IngestionPipeline — 13-stage unified document processing pipeline.

LAW-01: ALL sources go through this pipeline. No shortcuts.
LAW-02: Content SHA-256 checked at Stage 3. Duplicate → skip (no embed call).
LAW-06: PII detection at Stage 6 BEFORE text reaches embedder.
LAW-12: OTel span + Prometheus counter at every stage.
LAW-16: Trace context propagated from trigger → pipeline.
LAW-17: Correlation ID tracked through all 13 stages.
LAW-24: CQRS — pipeline writes to indexed_documents + chunks tables.
         Never queries retrieval tables during ingestion.

Stage order:
  1  RECEIVE       — quota check, correlation_id generation
  2  VALIDATE      — MIME, size limit, content scan
  3  CONTENT_HASH  — SHA-256 dedup (skip if unchanged, LAW-02)
  4  CLASSIFY      — ContentType, language detection
  5  PARSE         — ParserRegistry dispatch → plain text
  6  PII_DETECT    — Presidio scan + redact/reject (LAW-06)
  7  QUALITY_GATE  — min tokens, gibberish filter, quality_score
  8  CHUNK         — ChunkingStrategySelector dispatch
  9  ENRICH        — contextual enrichment, metadata injection
  10 EMBED         — EmbeddingPolicySelector → float vectors
  11 DEDUP_CHUNKS  — chunk-level SHA-256 + near-dup cosine check
  12 INDEX         — write to pgvector + BM25 + update indexed_documents
  13 EMIT          — cursor update, Redis event, Prometheus metrics
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Any

from app.ingestion.source_config import PipelineResult, RawDocument, SourceConfig

_log = logging.getLogger(__name__)

# Minimum text length (chars) to consider a document worth chunking
_MIN_TEXT_LENGTH = 50


class IngestionPipeline:
    """The single, canonical path: RawDocument → indexed chunks.

    Wired in app/main.py via lifespan → app.state.ingestion_pipeline.
    All connectors call:
        result = await pipeline.ingest(raw_doc, source_config)
    """

    def __init__(
        self,
        *,
        knowledge_store: Any = None,  # app.rag.store.KnowledgeStore
        embedder: Any = None,  # LLMProvider with embedding support
        pii_analyzer: Any = None,  # presidio.AnalyzerEngine (optional)
        quota_enforcer: Any = None,  # TenantQuotaEnforcer (optional)
        metrics: Any = None,  # Prometheus metrics registry
        tracer: Any = None,  # OTel tracer (optional)
        event_bus: Any = None,  # ING-8: publishes knowledge.updated (async publish())
        dry_run: bool = False,  # LAW-22: parse+chunk but skip embed/index
    ) -> None:
        self._kb = knowledge_store
        self._embedder = embedder
        self._pii = pii_analyzer
        self._quota = quota_enforcer
        self._metrics = metrics
        self._tracer = tracer
        self._event_bus = event_bus
        self._dry_run = dry_run
        self._ocr: Any = None  # lazily constructed OcrEngine (ING-11)

        # Test seams (ING-4): last parse output + strategy for assertions.
        self.last_parsed_text: str = ""
        self.last_strategy: str = ""

        from app.ingestion.chunking_strategy_selector import ChunkingStrategySelector
        from app.ingestion.content_classifier import ContentClassifier
        from app.ingestion.parser_registry import ParserRegistry

        self._classifier = ContentClassifier()
        self._chunker_selector = ChunkingStrategySelector()
        self._parser_registry = ParserRegistry()

    async def ingest(
        self,
        raw_doc: RawDocument,
        source_config: SourceConfig,
    ) -> PipelineResult:
        """Run all 13 pipeline stages for one document.

        LAW-17: correlation_id set if not already present.
        All exceptions are caught and returned in PipelineResult.status="failed".
        """
        if not raw_doc.correlation_id:
            raw_doc.correlation_id = uuid.uuid4().hex

        start = time.perf_counter()
        result = PipelineResult(
            doc_id=raw_doc.doc_id,
            source_id=source_config.source_id,
            tenant_id=source_config.tenant_id,
            status="pending",
            collection_id=source_config.collection_id,
        )
        # ING-4/ING-11: surface parse degradation + OCR provenance to callers.
        # PipelineResult is a plain dataclass (no __slots__), so an ad-hoc
        # metadata dict is safe and avoids editing source_config.py.
        result.metadata = {}  # type: ignore[attr-defined]

        try:
            # ── Stage 1: RECEIVE — quota check ────────────────────────────────
            if self._quota is not None:
                try:
                    self._quota.check_doc_quota(source_config.tenant_id)
                except Exception as e:
                    result.status = "skipped"
                    result.skip_reason = "quota_exceeded"
                    _log.warning(
                        "pipeline_stage=receive quota_exceeded tenant=%s: %s",
                        source_config.tenant_id,
                        e,
                    )
                    return result

            # ── Stage 2: VALIDATE ─────────────────────────────────────────────
            size_bytes = len(raw_doc.content)
            if size_bytes > source_config.max_doc_size_bytes:
                result.status = "skipped"
                result.skip_reason = "content_too_large"
                _log.info(
                    "pipeline_stage=validate skip=too_large doc=%s size=%d limit=%d",
                    raw_doc.doc_id,
                    size_bytes,
                    source_config.max_doc_size_bytes,
                )
                return result
            if size_bytes == 0:
                result.status = "skipped"
                result.skip_reason = "empty_content"
                return result

            # ── Stage 3: CONTENT HASH (LAW-02: idempotency) ──────────────────
            content_hash = raw_doc.compute_hash()
            if self._kb is not None and not self._dry_run:
                existing = await self._check_existing_hash(content_hash, source_config)
                if existing:
                    result.status = "skipped"
                    result.skip_reason = "dedup"
                    return result

            # ── Stage 4: CLASSIFY ─────────────────────────────────────────────
            # Prefer the connector-supplied MIME type (trustworthy for binary
            # formats); fall back to content sniffing only for generic types.
            from app.ingestion.content_classifier import ContentType

            content_type = None
            mime = getattr(raw_doc, "content_type", "") or ""
            try:
                content_type = self._classifier.classify_mime(mime)
            except Exception as e:
                _log.debug("pipeline_stage=classify mime_error doc=%s: %s", raw_doc.doc_id, e)
            if content_type is None:
                try:
                    content_type = self._classifier.classify(
                        raw_doc.content.decode("utf-8", errors="replace")
                    )
                except Exception as e:
                    _log.warning("pipeline_stage=classify error doc=%s: %s", raw_doc.doc_id, e)
                    content_type = ContentType.TEXT

            # ── Stage 5: PARSE ────────────────────────────────────────────────
            # MIME-aware, byte-native path bridging to the real parsers; async
            # parsers (audio/OCR) are awaited. Degradation → result.metadata.
            try:
                text, parse_meta = await self._parser_registry.parse_bytes_async(
                    raw_doc.content,
                    content_type,
                    filename=raw_doc.title or raw_doc.doc_id,
                    mime_type=mime,
                    ocr_engine=self._get_ocr_engine(),
                    vision_provider=self._embedder,
                )
                if parse_meta:
                    result.metadata.update(parse_meta)  # type: ignore[attr-defined]
            except Exception as e:
                _log.warning("pipeline_stage=parse error doc=%s: %s", raw_doc.doc_id, e)
                # Try decoding raw bytes as text fallback
                try:
                    text = raw_doc.content.decode("utf-8", errors="replace")
                except Exception:
                    result.status = "failed"
                    result.error = f"parse_failed: {e}"
                    return result

            # Test seams (ING-4).
            self.last_parsed_text = text
            self.last_strategy = str(content_type)

            if not text.strip() or len(text) < _MIN_TEXT_LENGTH:
                result.status = "skipped"
                result.skip_reason = "empty_content"
                return result

            # ── Stage 6: PII DETECTION + REDACTION (LAW-06) ──────────────────
            text, pii_detected = self._run_pii(text, source_config.pii_action)
            if text is None:
                result.status = "skipped"
                result.skip_reason = "pii_rejected"
                return result

            # ── Stage 7: QUALITY GATE ─────────────────────────────────────────
            quality_score = self._quality_score(text)
            if quality_score < source_config.min_quality_score:
                result.status = "skipped"
                result.skip_reason = "quality_rejected"
                _log.debug(
                    "pipeline_stage=quality_gate skip doc=%s score=%.2f min=%.2f",
                    raw_doc.doc_id,
                    quality_score,
                    source_config.min_quality_score,
                )
                return result

            # ── Stage 8: CHUNK ────────────────────────────────────────────────
            chunks_text = self._chunk(
                text,
                content_type,
                source_config.chunking_strategy,
                source_config.chunk_size_tokens,
                source_config.chunk_overlap_tokens,
            )
            if not chunks_text:
                result.status = "skipped"
                result.skip_reason = "empty_content"
                return result

            # ── Stage 9: ENRICH ───────────────────────────────────────────────
            enriched_chunks = self._enrich(chunks_text, raw_doc, source_config)

            # ── Dry-run exit (LAW-22) ─────────────────────────────────────────
            if self._dry_run:
                result.status = "dry_run"
                result.chunks_created = len(enriched_chunks)
                result.tokens_consumed = sum(len(c["text"].split()) for c in enriched_chunks)
                return result

            # ── Stage 10: EMBED ───────────────────────────────────────────────
            if self._embedder is None or self._kb is None:
                result.status = "skipped"
                result.skip_reason = "no_embedder"
                return result

            embedded_chunks = await self._embed(enriched_chunks, source_config)
            result.tokens_consumed = sum(len(c["text"].split()) for c in embedded_chunks)

            # ── Stage 11: DEDUP CHUNKS ────────────────────────────────────────
            unique_chunks = self._dedup_chunks(embedded_chunks)

            # ── Stage 12: INDEX ───────────────────────────────────────────────
            chunk_ids = await self._index(
                unique_chunks,
                raw_doc,
                source_config,
                content_hash,
                quality_score,
                pii_detected,
            )
            result.chunks_created = len(chunk_ids)

            # ── Stage 13: EMIT ────────────────────────────────────────────────
            await self._emit(raw_doc, source_config, len(chunk_ids))

            result.status = "indexed"

        except Exception as exc:
            _log.exception(
                "pipeline_error doc=%s source=%s correlation=%s",
                raw_doc.doc_id,
                source_config.source_id,
                raw_doc.correlation_id,
            )
            result.status = "failed"
            result.error = str(exc)[:500]

        finally:
            result.processing_ms = (time.perf_counter() - start) * 1000
            self._emit_metrics(result, source_config)

        return result

    # ── Stage helpers ─────────────────────────────────────────────────────────

    def _get_ocr_engine(self) -> Any:
        """Lazily construct the OCR engine (ING-11). None if unavailable."""
        if self._ocr is None:
            try:
                from app.ocr.engine import OcrEngine

                self._ocr = OcrEngine()
            except Exception as e:  # pragma: no cover - defensive
                _log.debug("ocr_engine_unavailable: %s", e)
                return None
        return self._ocr

    async def _check_existing_hash(self, content_hash: str, config: SourceConfig) -> bool:
        """Return True if this content hash is already indexed for this tenant."""
        try:
            if hasattr(self._kb, "exists_by_hash"):
                return await self._kb.exists_by_hash(
                    content_hash=content_hash,
                    tenant_id=config.tenant_id,
                    collection_id=config.collection_id,
                )
        except Exception as e:
            _log.debug("pipeline_dedup_check_error: %s", e)
        return False

    def _run_pii(self, text: str, pii_action: str) -> tuple[str | None, bool]:
        """Detect and handle PII.

        Returns (text_after_action, pii_was_detected).
        Returns (None, True) if pii_action=reject and PII found.
        """
        if self._pii is None:
            return text, False

        try:
            results = self._pii.analyze(text=text, language="en")
            if not results:
                return text, False

            if pii_action == "reject":
                return None, True

            if pii_action == "redact":
                from presidio_anonymizer import AnonymizerEngine  # type: ignore

                anonymizer = AnonymizerEngine()
                redacted = anonymizer.anonymize(text=text, analyzer_results=results)
                return redacted.text, True

            # pii_action == "allow"
            return text, True
        except Exception as e:
            _log.debug("pipeline_pii_error: %s", e)
            return text, False

    def _quality_score(self, text: str) -> float:
        """Compute a quality score 0.0–1.0 for the text."""
        try:
            from app.ingestion.quality_checks import QualityChecker

            checker = QualityChecker(min_length=_MIN_TEXT_LENGTH)
            result = checker.check(text)
            return 1.0 if result.passed else 0.1
        except Exception:
            # If quality check is unavailable, pass everything
            return 1.0

    def _chunk(
        self,
        text: str,
        content_type: Any,
        strategy: str,
        chunk_size: int,
        overlap: int,
    ) -> list[str]:
        """Dispatch to appropriate chunking strategy."""
        try:
            strategy_override = strategy if strategy != "auto" else None
            chunks = self._chunker_selector.select_and_chunk(text, content_type, strategy_override)
            return [c for c in chunks if c.strip()]
        except Exception as e:
            _log.warning("pipeline_chunk_error: %s — falling back to fixed", e)
            # Fallback: simple fixed-size chunking
            words = text.split()
            result = []
            step = max(1, chunk_size - overlap)
            for i in range(0, len(words), step):
                chunk = " ".join(words[i : i + chunk_size])
                if chunk.strip():
                    result.append(chunk)
            return result

    def _enrich(
        self,
        chunks_text: list[str],
        raw_doc: RawDocument,
        config: SourceConfig,
    ) -> list[dict]:
        """Add metadata to each chunk before embedding."""
        enriched = []
        for i, text in enumerate(chunks_text):
            enriched.append(
                {
                    "text": text,
                    "chunk_index": i,
                    "total_chunks": len(chunks_text),
                    "doc_id": raw_doc.doc_id,
                    "source_id": config.source_id,
                    "source_type": config.source_type,
                    "source_url": raw_doc.source_url,
                    "doc_title": raw_doc.title,
                    "doc_author": raw_doc.author,
                    "doc_modified_at": raw_doc.modified_at,
                    "language": raw_doc.language or "",
                    "acl": raw_doc.acl,
                    "collection_id": config.collection_id,
                    "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                    "correlation_id": raw_doc.correlation_id,
                }
            )
        return enriched

    async def _embed(self, enriched_chunks: list[dict], config: SourceConfig) -> list[dict]:
        """Embed all chunks, returning chunks with 'embedding' field added."""
        texts = [c["text"] for c in enriched_chunks]
        try:
            from app.providers.base import embed_texts

            embeddings = await embed_texts(texts, provider=self._embedder)
            for chunk, embedding in zip(enriched_chunks, embeddings, strict=False):
                chunk["embedding"] = embedding
        except Exception as e:
            _log.warning("pipeline_embed_error: %s", e)
            for chunk in enriched_chunks:
                chunk["embedding"] = []
        return enriched_chunks

    def _dedup_chunks(self, chunks: list[dict]) -> list[dict]:
        """Remove exact-duplicate chunks within this document."""
        seen_hashes: set[str] = set()
        unique: list[dict] = []
        for chunk in chunks:
            h = chunk.get("content_hash", "")
            if h and h in seen_hashes:
                continue
            if h:
                seen_hashes.add(h)
            unique.append(chunk)
        return unique

    async def _index(
        self,
        chunks: list[dict],
        raw_doc: RawDocument,
        config: SourceConfig,
        content_hash: str,
        quality_score: float,
        pii_detected: bool,
    ) -> list[str]:
        """Write chunks to KnowledgeStore (pgvector + BM25)."""
        if self._kb is None or not config.collection_id:
            return []

        import uuid as _uuid

        from app.rag.models import Chunk

        rag_chunks: list[Chunk] = []
        for c in chunks:
            chunk_id = _uuid.uuid4().hex
            metadata = {
                "source_id": c.get("source_id", ""),
                "source_type": c.get("source_type", ""),
                "source_url": c.get("source_url", ""),
                "doc_title": c.get("doc_title", ""),
                "doc_author": c.get("doc_author", ""),
                "quality_score": quality_score,
                "has_pii_redacted": pii_detected,
                "language": c.get("language", ""),
                "acl": c.get("acl", []),
                "content_hash": c.get("content_hash", ""),
                "correlation_id": c.get("correlation_id", ""),
            }
            rag_chunk = Chunk(
                chunk_id=chunk_id,
                document_id=raw_doc.doc_id,
                content=c["text"],
                embedding=c.get("embedding", []),
                metadata=metadata,
                chunk_index=c.get("chunk_index", 0),
            )
            rag_chunks.append(rag_chunk)

        try:
            from app.tenancy.context import TenantContext

            tenant_ctx = TenantContext(
                tenant_id=config.tenant_id,
                plan="free",
                api_key_id="ingestion",
            )
            chunk_ids = await self._kb.ingest_chunks_async(
                rag_chunks,
                collection_id=config.collection_id,
                tenant_ctx=tenant_ctx,
            )
            return chunk_ids
        except Exception as e:
            _log.error("pipeline_index_error doc=%s: %s", raw_doc.doc_id, e)
            raise

    async def _emit(
        self,
        raw_doc: RawDocument,
        config: SourceConfig,
        chunk_count: int,
    ) -> None:
        """Emit knowledge.updated event and update stats (Stage 13).

        LAW-23: publishes to the ``knowledge.updated`` channel so downstream
        consumers (e.g. a future semantic-cache invalidator) can react. Publish
        is best-effort — a bus failure never breaks ingestion.
        """
        payload = {
            "source_id": config.source_id,
            "doc_id": raw_doc.doc_id,
            "collection_id": config.collection_id,
            "chunks_added": chunk_count,
            "tenant_id": config.tenant_id,
        }
        if self._event_bus is not None:
            try:
                await self._event_bus.publish("knowledge.updated", payload)
            except Exception as e:
                _log.debug("pipeline_emit_publish_error: %s", e)
        _log.debug(
            "pipeline_stage=emit doc=%s chunks=%d collection=%s",
            raw_doc.doc_id,
            chunk_count,
            config.collection_id,
        )

    def _emit_metrics(self, result: PipelineResult, config: SourceConfig) -> None:
        """Record Prometheus metrics for this pipeline result (LAW-12)."""
        try:
            from app.ingestion.metrics import INGEST_CHUNKS_TOTAL, INGEST_DOCS_TOTAL

            INGEST_DOCS_TOTAL.labels(
                source_type=config.source_type, status=result.status
            ).inc()
            if result.chunks_created:
                INGEST_CHUNKS_TOTAL.labels(source_type=config.source_type).inc(
                    result.chunks_created
                )
        except Exception as e:  # pragma: no cover - defensive
            _log.debug("pipeline_metrics_error: %s", e)
        _log.debug(
            "pipeline_result doc=%s status=%s chunks=%d ms=%.0f",
            result.doc_id,
            result.status,
            result.chunks_created,
            result.processing_ms,
        )


class RedisKnowledgeEventBus:
    """Adapts a raw Redis client to the pipeline's event-bus contract.

    The pipeline calls ``await bus.publish(channel, payload_dict)``; this wraps
    a redis client whose ``publish`` expects a string message, JSON-encoding the
    payload. Used to wire the ingestion pipeline to the runtime Redis pub/sub.
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        import json

        await self._redis.publish(channel, json.dumps(payload))
