# tests/ingestion/test_ingestion_orchestrator.py
"""Same ingestion API must accept all supported content types."""
from __future__ import annotations

import pytest

from app.ingestion.chunking_strategy_selector import ChunkingStrategySelector
from app.ingestion.content_classifier import ContentClassifier, ContentType
from app.ingestion.orchestrator import IngestionOrchestrator, IngestionResult
from app.tenancy.context import PlanTier, TenantContext


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def orchestrator():
    return IngestionOrchestrator()


# ── ContentClassifier ─────────────────────────────────────────────────────────

def test_classify_plain_text():
    classifier = ContentClassifier()
    result = classifier.classify("The quick brown fox jumps over the lazy dog.")
    assert result == ContentType.TEXT


def test_classify_python_code():
    classifier = ContentClassifier()
    result = classifier.classify("def hello():\n    return 'world'\n\nimport os")
    assert result == ContentType.CODE


def test_classify_html():
    classifier = ContentClassifier()
    result = classifier.classify("<html><body><h1>Hello</h1></body></html>")
    assert result == ContentType.HTML


def test_classify_markdown():
    classifier = ContentClassifier()
    result = classifier.classify("# Title\n\n- item 1\n- item 2\n\n1. ordered")
    assert result == ContentType.MARKDOWN


def test_classify_by_filename():
    classifier = ContentClassifier()
    assert classifier.classify_by_filename("report.pdf") == ContentType.PDF
    assert classifier.classify_by_filename("data.csv") == ContentType.CSV
    assert classifier.classify_by_filename("script.py") == ContentType.CODE
    assert classifier.classify_by_filename("document.docx") == ContentType.DOCX
    assert classifier.classify_by_filename("image.png") == ContentType.IMAGE
    assert classifier.classify_by_filename("audio.mp3") == ContentType.AUDIO
    assert classifier.classify_by_filename("video.mp4") == ContentType.VIDEO


# ── ChunkingStrategySelector ─────────────────────────────────────────────────

def test_text_gets_semantic_chunking():
    selector = ChunkingStrategySelector()
    strategy = selector.select(ContentType.TEXT)
    assert strategy == "semantic"


def test_code_gets_ast_chunking():
    selector = ChunkingStrategySelector()
    strategy = selector.select(ContentType.CODE)
    assert strategy == "ast"


def test_pdf_gets_layout_chunking():
    selector = ChunkingStrategySelector()
    strategy = selector.select(ContentType.PDF)
    assert strategy in ("layout", "page", "section")


def test_audio_gets_timestamp_chunking():
    selector = ChunkingStrategySelector()
    strategy = selector.select(ContentType.AUDIO)
    assert strategy == "timestamp"


def test_video_gets_scene_chunking():
    selector = ChunkingStrategySelector()
    strategy = selector.select(ContentType.VIDEO)
    assert strategy == "scene"


def test_csv_gets_row_group_chunking():
    selector = ChunkingStrategySelector()
    strategy = selector.select(ContentType.CSV)
    assert strategy in ("row_group", "table")


# ── IngestionOrchestrator ─────────────────────────────────────────────────────

async def test_ingest_text_returns_chunks(orchestrator, tenant_ctx):
    result = await orchestrator.ingest(
        content="The AgentVerse platform uses dynamic orchestration to route goals.",
        content_type="text",
        collection_id="col1",
        tenant_ctx=tenant_ctx,
        dry_run=True,
    )
    assert isinstance(result, IngestionResult)
    assert result.chunks_prepared >= 1
    assert result.chunks_created == 0
    assert not result.persisted
    assert result.content_type == ContentType.TEXT
    assert result.chunking_strategy == "semantic"


async def test_ingest_code_returns_chunks(orchestrator, tenant_ctx):
    code = "def calculate(x, y):\n    return x + y\n\nclass Calculator:\n    pass"
    result = await orchestrator.ingest(
        content=code,
        content_type="code",
        collection_id="col2",
        tenant_ctx=tenant_ctx,
        dry_run=True,
    )
    assert result.chunks_prepared >= 1
    assert result.chunking_strategy == "ast"


async def test_ingest_attaches_provenance(orchestrator, tenant_ctx):
    result = await orchestrator.ingest(
        content="Test content for provenance tracking.",
        content_type="text",
        collection_id="col3",
        tenant_ctx=tenant_ctx,
        source_url="https://source.example.com/doc1",
        dry_run=True,
    )
    assert result.source_url == "https://source.example.com/doc1"
    assert result.tenant_id == tenant_ctx.tenant_id


async def test_ingest_unknown_type_defaults_to_text(orchestrator, tenant_ctx):
    result = await orchestrator.ingest(
        content="Some content",
        content_type="unknown",
        collection_id="col4",
        tenant_ctx=tenant_ctx,
        dry_run=True,
    )
    assert result.chunks_prepared >= 1


def test_chunking_strategy_selector_all_types():
    """ChunkingStrategySelector must map all content types to correct strategies."""
    from app.ingestion.chunking_strategy_selector import ChunkingStrategySelector
    from app.ingestion.content_classifier import ContentType
    selector = ChunkingStrategySelector()
    expected = {
        ContentType.TEXT: "semantic",
        ContentType.CODE: "ast",
        ContentType.PDF: "layout",
        ContentType.AUDIO: "timestamp",
        ContentType.VIDEO: "scene",
        ContentType.CSV: "row_group",
        ContentType.DOCX: "paragraph",
        ContentType.IMAGE: "region",
    }
    for ct, strategy in expected.items():
        assert selector.select(ct) == strategy, f"{ct.value}: expected {strategy}"


async def test_orchestrator_auto_detects_type(orchestrator, tenant_ctx):
    """IngestionOrchestrator with content_type='auto' must detect HTML correctly."""
    html = "<html><body><p>Hello World</p></body></html>"
    from app.ingestion.content_classifier import ContentType
    result = await orchestrator.ingest(
        content=html,
        content_type="auto",
        collection_id="col1",
        tenant_ctx=tenant_ctx,
        dry_run=True,
    )
    assert result.content_type == ContentType.HTML


async def test_orchestrator_ingests_pdf_bytes(orchestrator, tenant_ctx):
    """IngestionOrchestrator must handle PDF content_type and produce chunks."""
    from app.ingestion.parsers.pdf_parser import PDFParser
    pdf_parser = PDFParser()
    result_text = "Sample PDF content for testing ingestion."
    pdf_result = pdf_parser.parse_text(result_text, "test.pdf")
    chunks = pdf_result.to_chunks()
    assert len(chunks) >= 1
    # Now ingest the extracted text as pdf type
    result = await orchestrator.ingest(
        content=result_text,
        content_type="pdf",
        collection_id="col1",
        tenant_ctx=tenant_ctx,
        source_url="https://example.com/report.pdf",
        dry_run=True,
    )
    assert result.chunks_prepared >= 1
    assert result.chunking_strategy == "layout"
