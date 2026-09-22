"""Hardening tests for real MIME parsers (ING-4), OCR (ING-11), EMIT (ING-8).

Strict TDD: these exercise the MIME-aware parse path, OCR wiring, and the
knowledge.updated event emission that replace the naive string parsers.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.content_classifier import ContentClassifier, ContentType
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _config(**kw) -> SourceConfig:
    base = {
        "source_id": "s1",
        "tenant_id": "t1",
        "name": "T",
        "family": SourceFamily.WEB,
        "source_type": "test",
    }
    base.update(kw)
    return SourceConfig(**base)  # type: ignore[arg-type]


# ── ING-4: classify_mime ──────────────────────────────────────────────────────


def test_classify_mime_maps_core_types():
    c = ContentClassifier()
    assert c.classify_mime("application/pdf") == ContentType.PDF
    assert c.classify_mime(DOCX_MIME) == ContentType.DOCX
    assert c.classify_mime("text/csv") == ContentType.CSV
    assert c.classify_mime("image/png") == ContentType.IMAGE
    assert c.classify_mime("audio/mpeg") == ContentType.AUDIO
    assert c.classify_mime("video/mp4") == ContentType.VIDEO


def test_classify_mime_ignores_params_and_unknown():
    c = ContentClassifier()
    assert c.classify_mime("text/csv; charset=utf-8") == ContentType.CSV
    assert c.classify_mime("text/plain") is None
    assert c.classify_mime("") is None


def test_classify_mime_returns_none_for_unsupported_dangerous_binary_types():
    """Executable/unknown-binary MIME types must not be misclassified as
    something ingestible -- classify_mime() returns None (unrecognised) so
    the caller falls back to content sniffing rather than treating the raw
    bytes of e.g. a Windows executable as a PDF/DOCX/image."""
    c = ContentClassifier()
    assert c.classify_mime("application/x-msdownload") is None
    assert c.classify_mime("application/octet-stream") is None
    assert c.classify_mime("application/x-executable") is None
    assert c.classify_mime("application/x-sh") is None


def test_pipeline_unsupported_binary_mime_falls_back_to_text_without_crashing():
    """An unsupported/unrecognised MIME type (classify_mime -> None) must not
    crash the pipeline. It degrades to content-sniffed classification
    (ContentType.TEXT for content that doesn't match any sniffing pattern)
    rather than raising or silently dropping the document with no trace."""
    import asyncio

    from app.ingestion.pipeline import IngestionPipeline
    from app.ingestion.source_config import RawDocument

    async def _run():
        pipeline = IngestionPipeline()
        raw = RawDocument(
            doc_id="d-exe",
            source_id="s1",
            tenant_id="t1",
            content=b"MZ\x90\x00\x03\x00\x00\x00 not a real document, just binary-ish bytes " * 20,
            content_type="application/x-msdownload",
        )
        config = _config()
        result = await pipeline.ingest(raw, config)
        # Must reach past classify+parse without raising -- whatever the
        # final status, it must not be a hard "failed" from an unhandled
        # exception in the classify stage.
        assert result.status in ("skipped", "success", "completed")
        assert pipeline.last_strategy == str(ContentType.TEXT)

    asyncio.run(_run())


# ── ING-4: real DOCX parser through the pipeline ──────────────────────────────


@pytest.mark.asyncio
async def test_real_docx_yields_paragraph_text_no_leakage():
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    for para in [
        "The quarterly revenue report shows strong growth across regions.",
        "Engineering delivered the new ingestion pipeline ahead of schedule.",
        "Customer satisfaction scores improved by twelve percent this period.",
    ]:
        doc.add_paragraph(para)
    buf = io.BytesIO()
    doc.save(buf)
    docx_bytes = buf.getvalue()

    pipeline = IngestionPipeline(dry_run=True)
    raw = RawDocument(
        doc_id="d1",
        source_id="s1",
        tenant_id="t1",
        content=docx_bytes,
        content_type=DOCX_MIME,
    )
    result = await pipeline.ingest(raw, _config())

    text = pipeline.last_parsed_text
    assert "quarterly revenue report" in text
    assert "ingestion pipeline ahead of schedule" in text
    # No raw OOXML leakage
    assert "PK" not in text
    assert "<w:" not in text
    assert result.status == "dry_run"
    assert result.chunks_created >= 1


# ── ING-4: real CSV parser through the pipeline ───────────────────────────────


@pytest.mark.asyncio
async def test_csv_yields_per_row_content():
    csv_bytes = (
        b"name,role,city\n"
        b"Alice,Engineer,Seattle\n"
        b"Bob,Designer,Portland\n"
        b"Carol,Manager,Denver\n"
    )
    pipeline = IngestionPipeline(dry_run=True)
    raw = RawDocument(
        doc_id="d2",
        source_id="s1",
        tenant_id="t1",
        content=csv_bytes,
        content_type="text/csv",
    )
    await pipeline.ingest(raw, _config())

    text = pipeline.last_parsed_text
    assert "name: Alice" in text
    assert "role: Engineer" in text
    assert "city: Denver" in text


# ── ING-11: OCR text flows into image chunks ──────────────────────────────────


@pytest.mark.asyncio
async def test_image_ocr_text_flows_into_chunks():
    from app.ocr.models import DocumentType, OcrResult

    ocr_text = (
        "INVOICE #4471 TOTAL DUE 1250 USD Acme Corp billing department net thirty terms"
    )

    async def fake_extract(self, *, image_bytes=None, pdf_bytes=None, provider=None):
        return OcrResult(
            raw_text=ocr_text,
            document_type=DocumentType.GENERAL,
            engine_used="tesseract",
            overall_confidence=0.9,
            page_count=1,
        )

    with patch("app.ocr.engine.OcrEngine.extract", fake_extract):
        pipeline = IngestionPipeline(dry_run=True)
        raw = RawDocument(
            doc_id="img1",
            source_id="s1",
            tenant_id="t1",
            content=b"\x89PNG\r\n\x1a\nfake-image-bytes",
            content_type="image/png",
        )
        await pipeline.ingest(raw, _config())

    assert "INVOICE #4471" in pipeline.last_parsed_text
    assert "1250 USD" in pipeline.last_parsed_text


@pytest.mark.asyncio
async def test_image_ocr_llm_fallback_recorded_in_metadata():
    from app.ocr.models import DocumentType, OcrResult

    async def fake_extract(self, *, image_bytes=None, pdf_bytes=None, provider=None):
        return OcrResult(
            raw_text="Text recovered by the vision model when tesseract is unavailable here.",
            document_type=DocumentType.GENERAL,
            engine_used="llm_vision",
            overall_confidence=0.8,
            page_count=1,
        )

    with patch("app.ocr.engine.OcrEngine.extract", fake_extract):
        pipeline = IngestionPipeline(dry_run=True)
        raw = RawDocument(
            doc_id="img2",
            source_id="s1",
            tenant_id="t1",
            content=b"\x89PNG\r\n\x1a\nfake",
            content_type="image/png",
        )
        result = await pipeline.ingest(raw, _config())

    assert result.metadata.get("ocr_engine") == "llm_vision"
    assert result.metadata.get("ocr_fallback") == "llm_vision"


# ── ING-8: EMIT publishes knowledge.updated ───────────────────────────────────


class _RecordingBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, channel: str, payload: dict) -> None:
        self.published.append((channel, payload))


@pytest.mark.asyncio
async def test_emit_publishes_knowledge_updated_with_chunk_count():
    mock_kb = MagicMock()
    mock_kb.exists_by_hash = AsyncMock(return_value=False)
    mock_kb.ingest_chunks_async = AsyncMock(return_value=["c1", "c2"])
    mock_embedder = MagicMock()
    bus = _RecordingBus()

    with patch(
        "app.providers.base.embed_texts",
        AsyncMock(return_value=[[0.1] * 8, [0.2] * 8]),
    ):
        pipeline = IngestionPipeline(
            knowledge_store=mock_kb, embedder=mock_embedder, event_bus=bus
        )
        content = ("Knowledge base ingestion emit test content sentence. " * 20).encode()
        raw = RawDocument(
            doc_id="d3",
            source_id="s1",
            tenant_id="t1",
            content=content,
            content_type="text/plain",
        )
        result = await pipeline.ingest(raw, _config(collection_id="c1"))

    assert result.status == "indexed"
    knowledge_events = [p for ch, p in bus.published if ch == "knowledge.updated"]
    assert len(knowledge_events) == 1
    assert knowledge_events[0]["chunks_added"] == result.chunks_created
    assert knowledge_events[0]["doc_id"] == "d3"


def test_ingest_metrics_module_exposes_counters():
    from app.ingestion import metrics

    assert hasattr(metrics, "INGEST_DOCS_TOTAL")
    assert hasattr(metrics, "INGEST_CHUNKS_TOTAL")
    # labels().inc() must be callable without raising (works with or without prometheus)
    metrics.INGEST_DOCS_TOTAL.labels(source_type="test", status="indexed").inc()
    metrics.INGEST_CHUNKS_TOTAL.labels(source_type="test").inc(3)
