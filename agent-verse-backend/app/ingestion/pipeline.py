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
from dataclasses import dataclass
from typing import Any

from app.agent.tokenizer import count_tokens
from app.ingestion.parser_registry import DocumentParseError
from app.ingestion.source_config import PipelineResult, RawDocument, SourceConfig

# Guardrails 2.0 integration
try:
    from app.guardrails_v2.engine import guardrails_engine
    from app.guardrails_v2.models import GuardrailLayer

    _GUARDRAILS_AVAILABLE = True
except ImportError:  # pragma: no cover - guardrails_v2 always ships with the app
    _GUARDRAILS_AVAILABLE = False
    guardrails_engine = None  # type: ignore[assignment]
    GuardrailLayer = None  # type: ignore[assignment]

_log = logging.getLogger(__name__)


_ORIGIN_MAX_KEYS = 12
_ORIGIN_MAX_VALUE = 256


def chunk_origin(metadata: dict[str, Any] | None) -> dict[str, str]:
    """The connector's ``metadata["origin"]`` as stored on every chunk.

    A small flat mapping of strings (kind + the producing record's ids): what a
    search hit cites. Anything else a connector puts there is dropped.
    """
    raw = (metadata or {}).get("origin")
    if not isinstance(raw, dict):
        return {}
    origin: dict[str, str] = {}
    for key, value in list(raw.items())[:_ORIGIN_MAX_KEYS]:
        if isinstance(key, str) and isinstance(value, str | int | float) and str(value):
            origin[key[:64]] = str(value)[:_ORIGIN_MAX_VALUE]
    return origin


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length embedding vectors (0.0 on degenerate input)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / ((na**0.5) * (nb**0.5))


@dataclass(frozen=True)
class ScreenResult:
    """Outcome of :meth:`IngestionPipeline.screen_text` (Stages 6 + 6b)."""

    text: str
    pii_detected: bool = False
    # "" | "pii_rejected" | "guardrail_blocked" | "guardrail_review_required"
    blocked_reason: str = ""


# Length no longer gates indexing: a short document with real content (a one-line
# policy, a product-code page, ``tag: urgent``) is indexed; only empty, noise or
# page-boilerplate text is skipped (``quality_checks.boilerplate_reason``). It used
# to drop every parsed text under 50 characters as ``empty_content``.


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
        kg_hook: Any = None,  # KGIngestionHook (optional) — D-15 auto-population
        kg_provider: Any = None,  # LLMProvider for KG extraction (optional)
    ) -> None:
        self._kb = knowledge_store
        self._embedder = embedder
        self._pii = pii_analyzer
        self._quota = quota_enforcer
        self._metrics = metrics
        self._tracer = tracer
        self._event_bus = event_bus
        self._dry_run = dry_run
        self._kg_hook = kg_hook
        self._kg_provider = kg_provider
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

    async def run(
        self,
        raw_doc: RawDocument,
        *,
        tenant_context: Any = None,
        source_config: SourceConfig | None = None,
    ) -> PipelineResult:
        """Adapter for callers holding a RawDocument (e.g. the DLQ-retry path).

        P0-11: a SourceConfig is required to select the correct parser/chunker.
        Without one the document is cleanly *skipped* (``no_source_config``)
        rather than processed against a fabricated config or crashing.
        """
        if source_config is None:
            return PipelineResult(
                doc_id=raw_doc.doc_id,
                source_id=raw_doc.source_id,
                tenant_id=raw_doc.tenant_id,
                status="skipped",
                skip_reason="no_source_config",
            )
        return await self.ingest(raw_doc, source_config)

    async def ingest(
        self,
        raw_doc: RawDocument,
        source_config: SourceConfig,
        *,
        dry_run: bool | None = None,
        supersedes: str | None = None,
    ) -> PipelineResult:
        """Run all 13 pipeline stages for one document.

        ``supersedes`` (D2): the id of a document this one takes over — a
        MongoDB document stored under its pre-v8 id. Stage 3 then dedups only
        against content held by a THIRD document, and Stage 12 deletes the
        superseded document in the same transaction that writes this one.

        LAW-17: correlation_id set if not already present.
        All exceptions are caught and returned in PipelineResult.status="failed".

        ``dry_run`` (LAW-22) applies to this call only; ``None`` uses the mode the
        pipeline was constructed with. The pipeline is shared app-wide, so a
        preview must never toggle it for everyone (PREVIEW-RACE).
        """
        dry = self._dry_run if dry_run is None else dry_run
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

        from app.ingestion.source_config import (
            CONNECTOR_FAILURE_KEY,
            CONNECTOR_FAILURE_RETRYABLE_KEY,
        )

        connector_failure = (raw_doc.metadata or {}).get(CONNECTOR_FAILURE_KEY)
        if connector_failure:
            # The connector could not read this document; report it (the sync
            # records the failure and its reason) — never index an empty stand-in.
            result.status = "failed"
            result.error = f"connector: {connector_failure}"
            retryable = (raw_doc.metadata or {}).get(CONNECTOR_FAILURE_RETRYABLE_KEY)
            if retryable is not None:
                result.error += " (retryable)" if retryable else " (permanent)"
            result.processing_ms = (time.perf_counter() - start) * 1000
            return result

        try:
            # ── Stage 1: RECEIVE — quota check ────────────────────────────────
            # ``check_doc_quota`` is async for the DB-backed enforcer
            # (app.ingestion.quota). Only a genuine quota breach is a skip; any
            # other error (DB down) propagates to the outer handler and is
            # reported as ``failed`` (→ DLQ, retried) — it used to be swallowed
            # and mislabelled ``quota_exceeded``.
            if self._quota is not None:
                from app.ingestion.quota import IngestionQuotaExceededError, call_quota_check

                try:
                    await call_quota_check(self._quota, source_config.tenant_id)
                except IngestionQuotaExceededError as e:
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
            # A document re-keyed by its connector (S3: ``s3://bucket/key`` →
            # Source-scoped id) takes over its legacy copy when Stage 12 writes
            # it. Stage 3 stays document-scoped for it (P1b-6): another Source's
            # copy of the same bytes is not a reason to skip this one.
            legacy_takeover: str | None = None
            if not supersedes and self._kb is not None and not dry:
                legacy_takeover = await self._owned_legacy_document(raw_doc, source_config)
            if self._kb is not None and not dry and supersedes:
                # Errors propagate (outer handler -> failed): the caller deletes
                # the superseded copy only after a confirmed write.
                holder = await self._kb.document_id_by_hash(
                    content_hash=content_hash,
                    tenant_id=source_config.tenant_id,
                    collection_id=source_config.collection_id,
                )
                if holder is not None and holder != supersedes:
                    result.status = "skipped"
                    result.skip_reason = "dedup"
                    return result
            elif self._kb is not None and not dry:
                existing = await self._check_existing_hash(
                    content_hash, source_config, doc_id=raw_doc.doc_id
                )
                if existing:
                    result.status = "skipped"
                    result.skip_reason = "dedup"
                    return result

            if legacy_takeover:
                supersedes = legacy_takeover
                result.metadata["superseded_legacy_id"] = legacy_takeover  # type: ignore[attr-defined]

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
                # The file name / signature before sniffing decoded text: a
                # Parquet/Avro/notebook sent as octet-stream used to be sniffed
                # as TEXT/JSON and indexed as raw bytes / raw notebook JSON.
                content_type = self._classify_by_name_or_signature(raw_doc)
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
            except DocumentParseError as e:
                # A binary / structured document that cannot be parsed fails; its
                # bytes are never decoded and indexed as text instead.
                result.status = "failed"
                result.error = f"parse_failed: {e}"
                return result
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

            from app.ingestion.quality_checks import boilerplate_reason

            not_content = boilerplate_reason(text)
            if not_content is not None:
                result.status = "skipped"
                result.skip_reason = "empty_content"
                result.metadata["empty_reason"] = not_content  # type: ignore[attr-defined]
                return result

            # ── Stage 6 + 6b: PII (LAW-06) + Guardrails 2.0 RAG_INGEST ────────
            screened = await self.screen_text(
                text,
                tenant_id=source_config.tenant_id,
                pii_action=source_config.pii_action,
                doc_id=raw_doc.doc_id,
            )
            if screened.blocked_reason:
                result.status = "skipped"
                result.skip_reason = screened.blocked_reason
                return result
            text = screened.text
            pii_detected = screened.pii_detected

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
            if dry:
                result.status = "dry_run"
                result.chunks_created = len(enriched_chunks)
                result.tokens_consumed = sum(count_tokens(c["text"]) for c in enriched_chunks)
                return result

            # ── Stage 10: EMBED ───────────────────────────────────────────────
            if self._embedder is None or self._kb is None:
                result.status = "skipped"
                result.skip_reason = "no_embedder"
                return result

            embedded_chunks = await self._embed(enriched_chunks, source_config)
            result.tokens_consumed = sum(count_tokens(c["text"]) for c in embedded_chunks)

            # ── Stage 11: DEDUP CHUNKS ────────────────────────────────────────
            _quant_mode = "none"
            if source_config.near_dup_threshold > 0.0:
                try:
                    from app.core.config import get_settings

                    _quant_mode = str(getattr(get_settings(), "embedding_quantization", "none"))
                except Exception:  # pragma: no cover - defensive; keep full precision
                    _quant_mode = "none"
            unique_chunks = self._dedup_chunks(
                embedded_chunks,
                near_dup_threshold=source_config.near_dup_threshold,
                quantization_mode=_quant_mode,
            )

            # ── Stage 11b: EMBEDDING INTEGRITY (D-12) ─────────────────────────
            # ``embed_texts`` returns an empty list (never zero/noise vectors)
            # when embedding is unavailable. Drop those chunks so we never index
            # a silent zero-vector; if nothing survives, skip honestly rather
            # than writing empty embeddings.
            _all_chunks = unique_chunks
            unique_chunks = [c for c in unique_chunks if c.get("embedding")]
            if not unique_chunks:
                # PROV-08: a failure, with the embedder's reason — not a quiet skip.
                _reason = next(
                    (c["embed_error"] for c in _all_chunks if c.get("embed_error")),
                    "the embedder returned no vectors",
                )
                result.status = "failed"
                result.skip_reason = "embedding_unavailable"
                result.error = f"embedding_unavailable: {_reason}"
                return result
            if len(unique_chunks) < len(_all_chunks):
                result.error = (
                    f"partial: {len(_all_chunks) - len(unique_chunks)} of "
                    f"{len(_all_chunks)} chunks had no embedding and were not indexed"
                )

            # ── Stage 12: INDEX ───────────────────────────────────────────────
            # ``result.metadata`` carries the parse/OCR/degradation provenance
            # gathered at Stage 5; persist it (and the document-level hash) onto
            # every indexed chunk so dedup + provenance survive re-ingest.
            provenance = dict(getattr(result, "metadata", {}) or {})
            from app.rag.store import DuplicateContentError

            try:
                chunk_ids = await self._index(
                    unique_chunks,
                    raw_doc,
                    source_config,
                    content_hash,
                    quality_score,
                    pii_detected,
                    provenance,
                    supersedes=supersedes,
                )
            except DuplicateContentError:
                # Lost the race against a concurrent identical ingestion (a retry
                # overlapping the original attempt, or a re-sync overlapping a
                # manual sync) that committed first — the content IS indexed,
                # just not by this call. Treat exactly like the Stage 3 dedup
                # skip rather than a pipeline failure (no DLQ, no retry storm).
                result.status = "skipped"
                result.skip_reason = "dedup"
                return result
            result.chunks_created = len(chunk_ids)

            # ── D-15: KG auto-population — extract entities/relations per indexed
            # doc. Guarded: never fail ingestion on a KG extraction error.
            if self._kg_hook is not None and chunk_ids:
                try:
                    # Each chunk goes with the id it was indexed under (the
                    # store returns them in order), so graph nodes point at the
                    # exact chunk GraphRAG seeds from.
                    _kg_pairs = [
                        (str(c.get("text", "")), str(cid))
                        for c, cid in zip(unique_chunks, chunk_ids, strict=False)
                        if c.get("text")
                    ]
                    if _kg_pairs:
                        _kg_summary = await self._kg_hook.process(
                            chunks=[text for text, _cid in _kg_pairs],
                            chunk_ids=[cid for _text, cid in _kg_pairs],
                            document_id=raw_doc.doc_id,
                            tenant_id=source_config.tenant_id,
                            provider=self._kg_provider,
                        )
                        result.kg_entities = int(_kg_summary.get("entities", 0))
                        result.kg_relations = int(_kg_summary.get("relations", 0))
                except Exception as _kg_exc:
                    _log.warning(
                        "pipeline_stage=kg_populate doc=%s kg_extraction_failed: %s",
                        raw_doc.doc_id,
                        str(_kg_exc)[:200],
                    )

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

    async def _owned_legacy_document(
        self, raw_doc: RawDocument, config: SourceConfig
    ) -> str | None:
        """The legacy id (``CONNECTOR_LEGACY_DOC_ID_KEY``) this document replaces, or None.

        Only when a document is indexed under that id in the collection AND it
        is attributed to this Source: another Source's copy of the same object
        is never taken over (it stays that Source's document). A lookup error
        propagates (the document fails and is retried) rather than leaving a
        duplicate beside the legacy copy.
        """
        from app.ingestion.source_config import CONNECTOR_LEGACY_DOC_ID_KEY

        legacy = str((raw_doc.metadata or {}).get(CONNECTOR_LEGACY_DOC_ID_KEY) or "")
        if not legacy or legacy == raw_doc.doc_id or not config.collection_id:
            return None
        lookup = getattr(self._kb, "get_document_source_async", None)
        if lookup is None:
            return None
        from app.tenancy.context import PlanTier, TenantContext

        source = await lookup(
            legacy,
            collection_id=config.collection_id,
            tenant_ctx=TenantContext(
                tenant_id=config.tenant_id, plan=PlanTier.FREE, api_key_id="ingestion"
            ),
        )
        if source and str(source.get("source_id") or "") == config.source_id:
            return legacy
        return None

    async def _check_existing_hash(
        self, content_hash: str, config: SourceConfig, *, doc_id: str = ""
    ) -> bool:
        """True if THIS document is already indexed with this content (unchanged).

        Scoped to the document (P1b-6): the same bytes under another key (a
        backup copy, a second Source on the same prefix) used to be skipped as
        "dedup", so that object was never represented — and when the indexed
        copy was deleted upstream, reconciliation removed the only document and
        the content vanished although the other copy still existed.
        """
        try:
            if hasattr(self._kb, "exists_by_hash"):
                kwargs: dict[str, Any] = {
                    "content_hash": content_hash,
                    "tenant_id": config.tenant_id,
                    "collection_id": config.collection_id,
                }
                if doc_id:
                    kwargs["document_id"] = doc_id
                return bool(await self._kb.exists_by_hash(**kwargs))
        except Exception as e:
            _log.debug("pipeline_dedup_check_error: %s", e)
        return False

    async def screen_text(
        self,
        text: str,
        *,
        tenant_id: str,
        pii_action: str = "redact",
        doc_id: str = "",
    ) -> ScreenResult:
        """Stages 6 + 6b for one document's text, before it is chunked/embedded.

        Public so the direct ingestion routes (``/knowledge/ingest*``) that do
        their own parsing/chunking still run the SAME PII + RAG_INGEST guardrail
        gate as connector ingestion (LAW-01/LAW-06) instead of bypassing it.

        Returns the (possibly redacted) text, whether PII was found, and a
        ``blocked_reason`` (``pii_rejected`` / ``guardrail_blocked``) when the
        document must not be indexed at all. Raises
        :class:`IngestionScreeningUnavailableError` when the RAG_INGEST guardrail
        cannot run (engine error, tenant rules not loadable) — fail closed.
        """
        # ── Stage 6: PII DETECTION + REDACTION (LAW-06) ──────────────────────
        original = text
        screened, pii_detected = self._run_pii(text, pii_action)
        if screened is None:
            return ScreenResult(text="", pii_detected=True, blocked_reason="pii_rejected")
        text = screened

        # ── Stage 6b: GUARDRAILS 2.0 — RAG_INGEST layer ───────────────────────
        # RAG_INGEST was declared in GuardrailLayer but never actually
        # checked anywhere before this fix, so compliance-bundle rules
        # that explicitly target it (GDPR's "Block PII in RAG ingest",
        # PCI's "Block PCI data") had zero real effect: unvetted document
        # content — including secrets, which Stage 6's PII scan does not
        # cover — flowed straight into chunking/embedding/persistence.
        # Scans the full parsed text (not truncated) — a document is
        # evaluated once, so a full regex pass is cheap and truncating would
        # let a secret past the halfway point of a long doc slip through.
        if _GUARDRAILS_AVAILABLE and guardrails_engine is not None:
            _require_guardrail_rule_repository()
            try:
                guardrails_engine.ensure_default_rules(tenant_id)
                # The guardrail sees the ORIGINAL text: evaluated after
                # redaction, a "block PII / secrets in RAG ingest" rule (the
                # GDPR and PCI bundles) could never fire, because Stage 6 had
                # already replaced what it looks for. Only an allowed document
                # continues, and it continues redacted.
                _g2_ingest_result = await guardrails_engine.evaluate(
                    content=original,
                    layer=GuardrailLayer.RAG_INGEST,
                    tenant_id=tenant_id,
                )
                if _g2_ingest_result.get("blocked"):
                    return ScreenResult(
                        text="", pii_detected=pii_detected, blocked_reason="guardrail_blocked"
                    )
                if _g2_ingest_result.get("hitl_required"):
                    # a03-F063-06: REQUIRE_HITL was ignored here and the document
                    # was indexed. Ingestion has no human in the loop, so the
                    # document is withheld (not indexed) for review — like the
                    # workflow guardrail treats hitl_required.
                    return ScreenResult(
                        text="",
                        pii_detected=pii_detected,
                        blocked_reason="guardrail_review_required",
                    )
                _g2_ingest_redacted = _g2_ingest_result.get("redacted_content")
                if _g2_ingest_redacted and _g2_ingest_redacted != original:
                    # A redacting rule fired. Apply the redaction to the
                    # PII-redacted text, not the original (its output would undo
                    # Stage 6) — without a second evaluate(), which recorded every
                    # violation twice and charged LLM-judge rules twice.
                    text = (
                        guardrails_engine.redact_text(text)
                        if text != original
                        else _g2_ingest_redacted
                    )
            except Exception as _g2_ingest_exc:
                # Fail CLOSED. This used to log and return the text as clean,
                # so an engine error — including GuardrailRulesUnavailableError
                # when the tenant's persisted rules could not be loaded — indexed
                # the document unscreened.
                _log.warning(
                    "pipeline_stage=guardrail_rag_ingest error doc=%s: %s",
                    doc_id,
                    _g2_ingest_exc,
                )
                raise IngestionScreeningUnavailableError(
                    f"RAG_INGEST guardrail unavailable: {_g2_ingest_exc}"
                ) from _g2_ingest_exc
        else:
            raise IngestionScreeningUnavailableError(
                "RAG_INGEST guardrail unavailable: guardrails engine not installed"
            )
        return ScreenResult(text=text, pii_detected=pii_detected)

    def _run_pii(self, text: str, pii_action: str) -> tuple[str | None, bool]:
        """Detect and handle PII.

        Returns (text_after_action, pii_was_detected).
        Returns (None, True) if pii_action=reject and PII found.

        The analyzer is ``app.ingestion.pii.RegexPIIAnalyzer`` (wired at every
        construction site) or any Presidio-compatible ``analyze()``. An analyzer
        exposing ``redact(text)`` redacts itself; otherwise Presidio's
        anonymizer is used. An analyzer *error* fails closed for ``redact`` /
        ``reject`` — the previous code returned the unscanned text as clean, so
        a broken analyzer silently indexed PII.
        """
        if self._pii is None:
            return text, False

        try:
            results = self._pii.analyze(text=text, language="en")
        except Exception as e:
            if pii_action == "allow":
                _log.debug("pipeline_pii_error (allow): %s", e)
                return text, False
            raise RuntimeError(f"pii_scan_failed: {e}") from e
        if not results:
            return text, False

        if pii_action == "reject":
            return None, True

        if pii_action == "redact":
            redact = getattr(self._pii, "redact", None)
            if callable(redact):
                return str(redact(text)), True
            from presidio_anonymizer import AnonymizerEngine  # type: ignore

            anonymizer = AnonymizerEngine()
            redacted = anonymizer.anonymize(text=text, analyzer_results=results)
            return redacted.text, True

        # pii_action == "allow"
        return text, True

    def _quality_score(self, text: str) -> float:
        """The checker's graded 0.0-1.0 score (so ``min_quality_score`` means
        something); 0.0 when the checker fails — it used to be a binary
        1.0 / 0.1 and 1.0 on a checker error, which passed everything."""
        try:
            from app.ingestion.quality_checks import (
                SHORT_TEXT_CHARS,
                QualityChecker,
                is_meaningful_text,
            )

            short = len(text.strip()) < SHORT_TEXT_CHARS
            checker = QualityChecker(min_length=1 if short else SHORT_TEXT_CHARS)
            score = float(checker.check(text).quality_score)
            if short:
                # Too short for a noise ratio to mean anything: real content
                # passes, boilerplate / noise does not.
                return 1.0 if is_meaningful_text(text) else 0.0
            return score
        except Exception as exc:
            _log.warning("pipeline_stage=quality checker_failed: %s", exc)
            return 0.0

    def _classify_by_name_or_signature(self, raw_doc: RawDocument) -> Any:
        """ContentType from the document's file name or byte signature, or None."""
        from urllib.parse import urlsplit

        from app.ingestion.content_classifier import ContentType

        metadata = raw_doc.metadata or {}
        for name in (
            raw_doc.title,
            str(metadata.get("name") or metadata.get("filename") or ""),
            urlsplit(raw_doc.source_url or "").path,
        ):
            if name and "." in name.rsplit("/", 1)[-1]:
                by_name = self._classifier.classify_by_filename(name)
                if by_name != ContentType.TEXT:
                    return by_name
        try:
            return self._classifier.classify_bytes(raw_doc.content)
        except Exception:
            return None

    def _chunk(
        self,
        text: str,
        content_type: Any,
        strategy: str,
        chunk_size: int,
        overlap: int,
    ) -> list[str]:
        """Dispatch to appropriate chunking strategy.

        An unsupported strategy name propagates (the document fails with that
        reason) — replacing it with fixed-size chunks would index the document
        under a strategy the Source never asked for.
        """
        from app.ingestion.chunkers import UnsupportedChunkingStrategyError

        try:
            strategy_override = strategy if strategy != "auto" else None
            chunks = self._chunker_selector.select_and_chunk(text, content_type, strategy_override)
            return [c for c in chunks if c.strip()]
        except UnsupportedChunkingStrategyError:
            raise
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
                    "origin": chunk_origin(raw_doc.metadata),
                }
            )
        return enriched

    async def _embed(self, enriched_chunks: list[dict], config: SourceConfig) -> list[dict]:
        """Embed all chunks, returning chunks with 'embedding' field added."""
        texts = [c["text"] for c in enriched_chunks]
        try:
            from types import SimpleNamespace

            from app.embedding.metering import embed_metered
            from app.providers.base import embed_texts
            from app.providers.embedder_factory import embedder_model_name

            model = embedder_model_name(self._embedder)

            async def _one_batch(batch: list[str]) -> list[list[float]]:
                return await embed_texts(batch, provider=self._embedder)

            # KB-40: bounded batches, each reserved against the tenant's budget
            # (the worker's / API's registered controller) and metered. A
            # refusal fails this document's embedding with the real reason.
            embeddings = await embed_metered(
                texts,
                _one_batch,
                tenant_ctx=SimpleNamespace(tenant_id=config.tenant_id),
                model=model,
                label=f"connector-sync:{config.source_id}",
            )
            # LAW-08: record which model produced each vector, so a model change
            # is detectable per chunk (and re-embeddable) instead of silently
            # mixing vector spaces.
            for chunk, embedding in zip(enriched_chunks, embeddings, strict=False):
                chunk["embedding"] = embedding
                chunk["embedding_model"] = model if embedding else ""
        except Exception as e:
            _log.warning("pipeline_embed_error: %s", e)
            for chunk in enriched_chunks:
                chunk["embedding"] = []
                # Carried to Stage 11b so the job fails with the real reason.
                chunk["embed_error"] = f"{type(e).__name__}: {e}"[:300]
        return enriched_chunks

    def _dedup_chunks(
        self,
        chunks: list[dict],
        *,
        near_dup_threshold: float = 0.0,
        quantization_mode: str = "none",
    ) -> list[dict]:
        """Remove duplicate chunks within this document (pipeline Stage 11).

        Always drops exact duplicates by ``content_hash``. When
        ``near_dup_threshold > 0``, additionally drops *semantic* near-duplicates:
        a chunk whose embedding similarity to an already-kept chunk is at or above
        the threshold is discarded (catches boilerplate/near-identical passages
        that survive exact-hash dedup). Chunks without an embedding are never
        dropped by the near-dup pass.

        ``quantization_mode`` (``none``/``int8``/``binary``) selects how the
        near-dup similarity is computed: full-precision cosine by default, or the
        cheaper quantized similarity (int8 tracks cosine closely; binary is a
        coarse first-stage estimate). Kept embeddings are encoded once.
        """
        from app.rag.quantization import EmbeddingQuantizer

        seen_hashes: set[str] = set()
        unique: list[dict] = []
        quantizer = EmbeddingQuantizer(quantization_mode)
        # Cache of already-kept embeddings in the active representation.
        kept_encoded: list[object] = []
        use_near_dup = near_dup_threshold > 0.0
        for chunk in chunks:
            h = chunk.get("content_hash", "")
            if h and h in seen_hashes:
                continue

            emb = chunk.get("embedding")
            if use_near_dup and emb:
                encoded = quantizer.encode(emb) if quantizer.enabled else emb
                is_near_dup = any(
                    (
                        quantizer.similarity(encoded, kept)
                        if quantizer.enabled
                        else _cosine_similarity(encoded, kept)  # type: ignore[arg-type]
                    )
                    >= near_dup_threshold
                    for kept in kept_encoded
                )
                if is_near_dup:
                    continue  # semantic near-duplicate of an already-kept chunk

            if h:
                seen_hashes.add(h)
            if use_near_dup and emb:
                kept_encoded.append(quantizer.encode(emb) if quantizer.enabled else emb)
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
        provenance: dict[str, Any] | None = None,
        *,
        supersedes: str | None = None,
    ) -> list[str]:
        """Write chunks to KnowledgeStore (pgvector + BM25).

        ``content_hash`` is the document-level SHA-256 (LAW-02); it is persisted
        on every chunk as ``doc_content_hash`` so ``KnowledgeStore.exists_by_hash``
        can dedup a re-ingest of the same document. ``provenance`` carries the
        parse/OCR/degradation metadata (ocr_used, ocr_engine, *_degraded, …) so
        downstream consumers see how the text was obtained; RPA/OCR agents set
        the same ``ingestion_provenance`` field on their own chunks.
        """
        # This used to ``return []`` and the document was then reported
        # ``status="indexed"`` with nothing written anywhere. Fail the document
        # instead so the sync/job shows the real outcome (and it lands in the DLQ).
        if self._kb is None:
            raise RuntimeError("no knowledge store is configured for this pipeline")
        if not config.collection_id:
            raise RuntimeError(
                f"source {config.source_id} has no collection_id; nothing can be indexed"
            )

        import uuid as _uuid

        from app.rag.models import Chunk

        prov = dict(provenance or {})
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
                "doc_content_hash": content_hash,
                "correlation_id": c.get("correlation_id", ""),
                "embedding_model": c.get("embedding_model", ""),
            }
            if c.get("origin"):
                # What produced the document (a goal, an approval, a workflow
                # run …): search hits cite it and erasure can find it (P1e).
                metadata["origin"] = c["origin"]
            if prov:
                metadata["ingestion_provenance"] = prov
                # Surface the OCR-used flag at the top level for cheap filtering.
                if prov.get("ocr_used"):
                    metadata["ocr_used"] = True
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
            from app.tenancy.context import PlanTier, TenantContext

            tenant_ctx = TenantContext(
                tenant_id=config.tenant_id,
                plan=PlanTier.FREE,
                api_key_id="ingestion",
            )
            # Connector document ids are stable per upstream item, so the same
            # id arriving again with new content is an edit: replace the old
            # version atomically (unchanged content was skipped at Stage 3).
            extra: dict[str, Any] = {}
            if supersedes:
                extra["supersedes_document_id"] = supersedes
            chunk_ids = await self._kb.ingest_chunks_async(
                rag_chunks,
                collection_id=config.collection_id,
                tenant_ctx=tenant_ctx,
                replace_document=True,
                duplicates_within_document=True,
                **extra,
            )
            return list(chunk_ids)
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


class IngestionScreeningUnavailableError(RuntimeError):
    """The ingestion screen (RAG_INGEST guardrail) could not run.

    Fail closed: the document is not indexed. Connector ingestion reports it as
    ``failed`` (DLQ, retried); the direct ingest routes answer 503.
    """


def _require_guardrail_rule_repository() -> None:
    """Refuse to screen in production when tenant guardrail rules can't be loaded.

    Without a bound repository ``ensure_tenant_loaded`` is a no-op, so only the
    baseline defaults would apply and a tenant's own RAG_INGEST block rules
    (GDPR / PCI bundles, custom rules) would be silently skipped. The API
    lifespan and the Celery worker both bind one; if that binding failed the
    screen fails closed rather than indexing under the wrong policy.
    """
    from app.core.config import get_settings

    if guardrails_engine is None or guardrails_engine.has_repository:
        return
    if get_settings().is_production:
        raise IngestionScreeningUnavailableError(
            "RAG_INGEST guardrail unavailable: tenant guardrail rules are not loadable "
            "(no rule repository bound)"
        )


class IngestionPolicyRejectedError(ValueError):
    """A document was refused by the ingestion PII / RAG_INGEST guardrail gate."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"document rejected by ingestion policy: {reason}")


_screening_pipeline: IngestionPipeline | None = None


def _default_screening_pipeline() -> IngestionPipeline:
    """A store-less pipeline used only for :func:`screen_ingest_text`."""
    global _screening_pipeline
    if _screening_pipeline is None:
        from app.ingestion.pii import build_pii_analyzer

        _screening_pipeline = IngestionPipeline(pii_analyzer=build_pii_analyzer())
    return _screening_pipeline


async def screen_ingest_text(
    text: str,
    *,
    tenant_id: str,
    pii_action: str = "redact",
    pipeline: Any = None,
    doc_id: str = "",
) -> str:
    """Run pipeline Stages 6 + 6b on *text* for an ingestion path that parses and
    chunks on its own (direct ``/knowledge/ingest*`` routes, the orchestrator,
    repository ingest), so none of them bypasses the PII + RAG_INGEST gate
    (LAW-01 / LAW-06).

    Uses the app's wired pipeline when it has a PII analyzer, else a store-less
    default. Returns the screened (possibly redacted) text; raises
    ``IngestionPolicyRejectedError`` when the document must not be indexed. A
    PII-scan failure propagates, and a guardrail that cannot run raises
    ``IngestionScreeningUnavailableError`` (fail closed).
    """
    screener = pipeline if isinstance(pipeline, IngestionPipeline) else None
    if screener is None or screener._pii is None:
        screener = _default_screening_pipeline()
    result = await screener.screen_text(
        text, tenant_id=tenant_id, pii_action=pii_action, doc_id=doc_id
    )
    if result.blocked_reason:
        raise IngestionPolicyRejectedError(result.blocked_reason)
    return result.text
