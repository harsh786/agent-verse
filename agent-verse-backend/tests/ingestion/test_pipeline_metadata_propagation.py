"""End-to-end metadata/provenance propagation through IngestionPipeline.

No existing test traces a document's provenance fields (source, doc title,
author, acl, language, correlation_id, and Stage-5 parse/OCR provenance) all
the way from a RawDocument through Chunk.metadata on the final persisted
Chunk objects. This test wires a real IngestionPipeline.ingest() run with a
mocked parser (returning OCR provenance metadata) and a mocked knowledge
store, and asserts every provenance field survives to the chunk actually
handed to the store.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from app.rag.models import Chunk


@pytest.mark.asyncio
async def test_metadata_and_ocr_provenance_propagate_to_final_chunks() -> None:
    mock_kb = MagicMock()
    mock_kb.exists_by_hash = AsyncMock(return_value=False)
    mock_kb.ingest_chunks_async = AsyncMock(return_value=["c1", "c2"])

    mock_embedder = MagicMock()

    pipeline = IngestionPipeline(knowledge_store=mock_kb, embedder=mock_embedder)

    parsed_text = (
        "Scanned invoice content recovered via OCR for the metadata "
        "propagation regression test. " * 5
    )
    parse_meta = {
        "ocr_used": True,
        "ocr_engine": "tesseract",
        "pages_processed": 3,
        "text_degraded": False,
    }
    pipeline._parser_registry.parse_bytes_async = AsyncMock(  # type: ignore[method-assign]
        return_value=(parsed_text, parse_meta)
    )

    raw = RawDocument(
        doc_id="doc-prov-1",
        source_id="src-42",
        tenant_id="tenant-9",
        content=b"raw scanned bytes (irrelevant, parser is mocked)",
        content_type="application/pdf",
        title="Q3 Invoice Scan",
        source_url="https://files.example.com/invoices/q3.pdf",
        author="finance-bot",
        modified_at="2026-08-01T00:00:00Z",
        language="en",
        acl=["role:finance", "role:admin"],
    )
    config = SourceConfig(
        source_id="src-42",
        tenant_id="tenant-9",
        name="Finance Docs",
        family=SourceFamily.DOCUMENT_STORE,
        source_type="gdrive",
        collection_id="col-finance",
    )

    with patch(
        "app.providers.base.embed_texts",
        AsyncMock(return_value=[[0.1] * 8, [0.2] * 8]),
    ):
        result = await pipeline.ingest(raw, config)

    assert result.status == "indexed", result.error
    mock_kb.ingest_chunks_async.assert_called_once()

    call_args, call_kwargs = mock_kb.ingest_chunks_async.call_args
    persisted_chunks: list[Chunk] = call_args[0]
    assert persisted_chunks, "expected at least one persisted chunk"
    assert call_kwargs["collection_id"] == "col-finance"

    expected_doc_hash = raw.content_hash  # set by compute_hash() at Stage 3
    assert expected_doc_hash  # sanity: Stage 3 ran

    for chunk in persisted_chunks:
        md = chunk.metadata
        # ── source identity ──────────────────────────────────────────────
        assert md["source_id"] == "src-42"
        assert md["source_type"] == "gdrive"
        assert md["source_url"] == "https://files.example.com/invoices/q3.pdf"
        # ── document-level provenance (RawDocument fields) ───────────────
        assert md["doc_title"] == "Q3 Invoice Scan"
        assert md["doc_author"] == "finance-bot"
        assert md["language"] == "en"
        assert md["acl"] == ["role:finance", "role:admin"]
        # ── correlation / hashing (LAW-17 / LAW-02) ───────────────────────
        assert md["correlation_id"] == raw.correlation_id
        assert md["doc_content_hash"] == expected_doc_hash
        assert md["content_hash"]  # per-chunk SHA-256, non-empty
        # ── quality / PII gate outcomes ────────────────────────────────────
        assert md["quality_score"] == 1.0
        assert md["has_pii_redacted"] is False
        # ── Stage 5 parse/OCR provenance (D-ing: ocr_used surfaced twice) ──
        assert md["ingestion_provenance"] == parse_meta
        assert md["ocr_used"] is True
        # ── the chunk itself carries the real document id + a real vector ──
        assert chunk.document_id == "doc-prov-1"
        assert chunk.embedding and len(chunk.embedding) == 8


@pytest.mark.asyncio
async def test_no_ocr_provenance_omits_ocr_used_flag() -> None:
    """When Stage 5 reports no provenance at all (e.g. a plain-text parse with
    no OCR), the top-level 'ocr_used' convenience flag must NOT be set — it
    should only appear when provenance says OCR actually ran."""
    mock_kb = MagicMock()
    mock_kb.exists_by_hash = AsyncMock(return_value=False)
    mock_kb.ingest_chunks_async = AsyncMock(return_value=["c1"])
    mock_embedder = MagicMock()

    pipeline = IngestionPipeline(knowledge_store=mock_kb, embedder=mock_embedder)
    pipeline._parser_registry.parse_bytes_async = AsyncMock(  # type: ignore[method-assign]
        return_value=("Plain text content with no OCR involved at all. " * 5, {})
    )

    raw = RawDocument(
        doc_id="doc-plain-1",
        source_id="src-1",
        tenant_id="tenant-1",
        content=b"hello world",
        content_type="text/plain",
    )
    config = SourceConfig(
        source_id="src-1",
        tenant_id="tenant-1",
        name="Plain",
        family=SourceFamily.WEB,
        source_type="web_crawl",
        collection_id="col-1",
    )

    with patch("app.providers.base.embed_texts", AsyncMock(return_value=[[0.1] * 4])):
        result = await pipeline.ingest(raw, config)

    assert result.status == "indexed"
    persisted_chunks = mock_kb.ingest_chunks_async.call_args[0][0]
    for chunk in persisted_chunks:
        assert "ingestion_provenance" not in chunk.metadata
        assert "ocr_used" not in chunk.metadata


@pytest.mark.asyncio
async def test_empty_acl_and_missing_title_propagate_as_empty_not_missing() -> None:
    """A RawDocument with unset provenance fields (defaults) must still
    produce well-formed metadata keys (empty string/list), never a KeyError
    downstream."""
    mock_kb = MagicMock()
    mock_kb.exists_by_hash = AsyncMock(return_value=False)
    mock_kb.ingest_chunks_async = AsyncMock(return_value=["c1"])
    mock_embedder = MagicMock()

    pipeline = IngestionPipeline(knowledge_store=mock_kb, embedder=mock_embedder)
    pipeline._parser_registry.parse_bytes_async = AsyncMock(  # type: ignore[method-assign]
        return_value=("Minimal document with no optional provenance set. " * 5, {})
    )

    raw = RawDocument(
        doc_id="doc-minimal-1",
        source_id="src-1",
        tenant_id="tenant-1",
        content=b"x",
        content_type="text/plain",
        # title, author, acl, language all left at their dataclass defaults
    )
    config = SourceConfig(
        source_id="src-1",
        tenant_id="tenant-1",
        name="Minimal",
        family=SourceFamily.WEB,
        source_type="web_crawl",
        collection_id="col-1",
    )

    with patch("app.providers.base.embed_texts", AsyncMock(return_value=[[0.1] * 4])):
        result = await pipeline.ingest(raw, config)

    assert result.status == "indexed"
    persisted_chunks = mock_kb.ingest_chunks_async.call_args[0][0]
    for chunk in persisted_chunks:
        assert chunk.metadata["doc_title"] == ""
        assert chunk.metadata["doc_author"] == ""
        assert chunk.metadata["acl"] == []
        assert chunk.metadata["language"] == ""
