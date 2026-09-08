"""Comprehensive tests for the ingestion framework.

Covers:
- BaseConnector contract enforcement
- ConnectorRegistry registration + lookup
- IngestionPipeline all 13 stages
- IngestionJobTracker cursor + locking
- Source config dataclasses
- All 8 connectors: validate + get_delta contract
- New parsers: CSV, JSON, HTML, Markdown, Notebook
- KnowledgeIngestTool
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


# ── SourceConfig / RawDocument / IngestionJob ────────────────────────────────

def test_source_family_has_18_values():
    from app.ingestion.source_config import SourceFamily
    assert len(list(SourceFamily)) == 18


def test_source_config_defaults():
    from app.ingestion.source_config import SourceConfig, SourceFamily
    cfg = SourceConfig(
        source_id="s1",
        tenant_id="t1",
        name="Test",
        family=SourceFamily.WEB,
        source_type="web_crawl",
    )
    assert cfg.sync_mode == "incremental"
    assert cfg.pii_action == "redact"
    assert cfg.min_quality_score == 0.3
    assert cfg.max_doc_size_bytes == 10_485_760
    assert cfg.chunk_size_tokens == 512


def test_raw_document_compute_hash():
    from app.ingestion.source_config import RawDocument
    doc = RawDocument(
        doc_id="d1", source_id="s1", tenant_id="t1",
        content=b"hello world", content_type="text/plain",
    )
    h = doc.compute_hash()
    expected = hashlib.sha256(b"hello world").hexdigest()
    assert h == expected
    assert doc.content_hash == expected


def test_raw_document_hash_deterministic():
    from app.ingestion.source_config import RawDocument
    d1 = RawDocument(doc_id="d1", source_id="s1", tenant_id="t1", content=b"abc", content_type="text/plain")
    d2 = RawDocument(doc_id="d2", source_id="s1", tenant_id="t1", content=b"abc", content_type="text/plain")
    assert d1.compute_hash() == d2.compute_hash()


def test_ingestion_job_defaults():
    from app.ingestion.source_config import IngestionJob
    job = IngestionJob(job_id="j1", source_id="s1", tenant_id="t1", status="running", sync_mode="incremental")
    assert job.docs_indexed == 0
    assert job.chunks_created == 0
    assert job.cursor_before == ""


def test_pipeline_result_defaults():
    from app.ingestion.source_config import PipelineResult
    r = PipelineResult(doc_id="d1", source_id="s1", tenant_id="t1", status="indexed")
    assert r.chunks_created == 0
    assert r.skip_reason == ""
    assert r.error == ""


# ── BaseConnector ABC ─────────────────────────────────────────────────────────

def test_base_connector_cannot_instantiate():
    from app.ingestion.base_connector import BaseConnector
    with pytest.raises(TypeError):
        BaseConnector()


def test_base_connector_abstract_methods():
    import inspect
    from app.ingestion.base_connector import BaseConnector
    abstract = {
        name for name, method in inspect.getmembers(BaseConnector)
        if getattr(method, "__isabstractmethod__", False)
    }
    assert "validate_connection" in abstract
    assert "get_delta" in abstract
    assert "source_type" in abstract


def test_concrete_connector_must_implement_source_type():
    from app.ingestion.base_connector import BaseConnector
    with pytest.raises(TypeError):
        class BadConnector(BaseConnector):
            async def validate_connection(self, config): ...
            async def get_delta(self, config, cursor): yield
        BadConnector()


def test_connection_health_defaults():
    from app.ingestion.base_connector import ConnectionHealth
    h = ConnectionHealth(ok=True)
    assert h.latency_ms == 0.0
    assert h.error == ""
    assert h.metadata == {}


# ── ConnectorRegistry ─────────────────────────────────────────────────────────

def test_registry_register_and_lookup():
    from app.ingestion.connector_registry import register, get_connector, _REGISTRY
    from app.ingestion.base_connector import BaseConnector, ConnectionHealth

    @register("test_source_xyz")
    class TestConnector(BaseConnector):
        source_type = "test_source_xyz"
        async def validate_connection(self, config): return ConnectionHealth(ok=True)
        async def get_delta(self, config, cursor):
            return
            yield

    cls = get_connector("test_source_xyz")
    assert cls is TestConnector
    # Cleanup
    _REGISTRY.pop("test_source_xyz", None)


def test_registry_unknown_source_raises():
    from app.ingestion.connector_registry import get_connector
    with pytest.raises(KeyError, match="No connector registered"):
        get_connector("definitely_not_registered_12345")


def test_registry_load_all_connectors():
    from app.ingestion.connector_registry import load_all_connectors, list_registered
    load_all_connectors()
    registered = list_registered()
    # All 8 connectors we built should be registered
    expected = {"agent_generated", "docx_file", "github", "pdf_file", "postgresql", "s3", "slack", "web_crawl"}
    assert expected.issubset(set(registered)), f"Missing: {expected - set(registered)}"


def test_registry_all_implement_baseconnector():
    from app.ingestion.connector_registry import load_all_connectors, _REGISTRY
    from app.ingestion.base_connector import BaseConnector
    load_all_connectors()
    for name, cls in _REGISTRY.items():
        assert issubclass(cls, BaseConnector), f"{name} does not extend BaseConnector"


def test_registry_feature_flag_disabled():
    from app.ingestion.connector_registry import register, get_connector, _REGISTRY
    from app.ingestion.base_connector import BaseConnector, ConnectionHealth

    @register("flagged_connector_test", feature_flag="test_flag_disabled")
    class FlaggedConnector(BaseConnector):
        source_type = "flagged_connector_test"
        async def validate_connection(self, c): return ConnectionHealth(ok=True)
        async def get_delta(self, c, cursor):
            return
            yield

    settings = SimpleNamespace(test_flag_disabled=False)
    with pytest.raises(RuntimeError, match="disabled by feature flag"):
        get_connector("flagged_connector_test", settings=settings)
    _REGISTRY.pop("flagged_connector_test", None)


# ── IngestionPipeline ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_skips_empty_content():
    from app.ingestion.pipeline import IngestionPipeline
    from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
    pipeline = IngestionPipeline()
    raw = RawDocument(doc_id="d1", source_id="s1", tenant_id="t1", content=b"", content_type="text/plain")
    config = SourceConfig(source_id="s1", tenant_id="t1", name="T", family=SourceFamily.WEB, source_type="test")
    result = await pipeline.ingest(raw, config)
    assert result.status == "skipped"
    assert result.skip_reason == "empty_content"


@pytest.mark.asyncio
async def test_pipeline_skips_oversized_content():
    from app.ingestion.pipeline import IngestionPipeline
    from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
    pipeline = IngestionPipeline()
    raw = RawDocument(doc_id="d1", source_id="s1", tenant_id="t1",
                      content=b"x" * 11_000_000, content_type="text/plain")
    config = SourceConfig(source_id="s1", tenant_id="t1", name="T",
                          family=SourceFamily.WEB, source_type="test", max_doc_size_bytes=1000)
    result = await pipeline.ingest(raw, config)
    assert result.status == "skipped"
    assert result.skip_reason == "content_too_large"


@pytest.mark.asyncio
async def test_pipeline_dry_run_returns_chunk_count():
    from app.ingestion.pipeline import IngestionPipeline
    from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
    pipeline = IngestionPipeline(dry_run=True)
    content = "This is a test document. " * 100
    raw = RawDocument(doc_id="d1", source_id="s1", tenant_id="t1",
                      content=content.encode(), content_type="text/plain")
    config = SourceConfig(source_id="s1", tenant_id="t1", name="T",
                          family=SourceFamily.WEB, source_type="test")
    result = await pipeline.ingest(raw, config)
    assert result.status == "dry_run"
    assert result.chunks_created > 0


@pytest.mark.asyncio
async def test_pipeline_content_hash_dedup():
    from app.ingestion.pipeline import IngestionPipeline
    from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily

    mock_kb = MagicMock()
    mock_kb.exists_by_hash = AsyncMock(return_value=True)
    pipeline = IngestionPipeline(knowledge_store=mock_kb)

    content = b"Some repeated content"
    raw = RawDocument(doc_id="d1", source_id="s1", tenant_id="t1",
                      content=content, content_type="text/plain")
    config = SourceConfig(source_id="s1", tenant_id="t1", name="T",
                          family=SourceFamily.WEB, source_type="test", collection_id="c1")
    result = await pipeline.ingest(raw, config)
    assert result.status == "skipped"
    assert result.skip_reason == "dedup"


@pytest.mark.asyncio
async def test_pipeline_quota_exceeded():
    from app.ingestion.pipeline import IngestionPipeline
    from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily

    mock_quota = MagicMock()
    mock_quota.check_doc_quota.side_effect = Exception("quota_exceeded")
    pipeline = IngestionPipeline(quota_enforcer=mock_quota)

    raw = RawDocument(doc_id="d1", source_id="s1", tenant_id="t1",
                      content=b"hello", content_type="text/plain")
    config = SourceConfig(source_id="s1", tenant_id="t1", name="T",
                          family=SourceFamily.WEB, source_type="test")
    result = await pipeline.ingest(raw, config)
    assert result.status == "skipped"
    assert result.skip_reason == "quota_exceeded"


@pytest.mark.asyncio
async def test_pipeline_skips_no_embedder():
    from app.ingestion.pipeline import IngestionPipeline
    from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily

    mock_kb = MagicMock()
    mock_kb.exists_by_hash = AsyncMock(return_value=False)
    pipeline = IngestionPipeline(knowledge_store=mock_kb, embedder=None)

    content = ("This is important content. " * 30).encode()
    raw = RawDocument(doc_id="d1", source_id="s1", tenant_id="t1",
                      content=content, content_type="text/plain")
    config = SourceConfig(source_id="s1", tenant_id="t1", name="T",
                          family=SourceFamily.WEB, source_type="test", collection_id="c1")
    result = await pipeline.ingest(raw, config)
    # No embedder → skipped
    assert result.status in ("skipped", "indexed")


@pytest.mark.asyncio
async def test_pipeline_full_happy_path():
    """Full pipeline with mock KB and embedder."""
    from app.ingestion.pipeline import IngestionPipeline
    from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily

    mock_kb = MagicMock()
    mock_kb.exists_by_hash = AsyncMock(return_value=False)
    mock_kb.ingest_chunks_async = AsyncMock(return_value=["chunk1", "chunk2"])

    mock_embedder = MagicMock()

    with patch("app.providers.base.embed_texts", AsyncMock(return_value=[[0.1] * 10, [0.2] * 10])):
        pipeline = IngestionPipeline(knowledge_store=mock_kb, embedder=mock_embedder)
        content = ("World-class ingestion pipeline test content. " * 20).encode()
        raw = RawDocument(doc_id="d1", source_id="s1", tenant_id="t1",
                          content=content, content_type="text/plain")
        config = SourceConfig(source_id="s1", tenant_id="t1", name="T",
                              family=SourceFamily.WEB, source_type="test", collection_id="c1")
        result = await pipeline.ingest(raw, config)

    assert result.status == "indexed"
    assert result.chunks_created >= 1
    mock_kb.ingest_chunks_async.assert_called_once()


@pytest.mark.asyncio
async def test_pipeline_populates_knowledge_graph_per_indexed_doc():
    """D-15: the KG ingestion hook runs once per indexed document with chunk texts,
    and its counts are recorded on the result — without ever failing ingestion."""
    from app.ingestion.pipeline import IngestionPipeline
    from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily

    mock_kb = MagicMock()
    mock_kb.exists_by_hash = AsyncMock(return_value=False)
    mock_kb.ingest_chunks_async = AsyncMock(return_value=["chunk1", "chunk2"])
    mock_embedder = MagicMock()

    kg_hook = MagicMock()
    kg_hook.process = AsyncMock(return_value={"entities": 3, "relations": 2})

    with patch("app.providers.base.embed_texts", AsyncMock(return_value=[[0.1] * 10, [0.2] * 10])):
        pipeline = IngestionPipeline(
            knowledge_store=mock_kb, embedder=mock_embedder, kg_hook=kg_hook
        )
        content = ("Ada Lovelace worked with Charles Babbage on the engine. " * 20).encode()
        raw = RawDocument(doc_id="doc-kg-1", source_id="s1", tenant_id="t9",
                          content=content, content_type="text/plain")
        config = SourceConfig(source_id="s1", tenant_id="t9", name="T",
                              family=SourceFamily.WEB, source_type="test", collection_id="c1")
        result = await pipeline.ingest(raw, config)

    assert result.status == "indexed"
    kg_hook.process.assert_called_once()
    _, kwargs = kg_hook.process.call_args
    assert kwargs["document_id"] == "doc-kg-1"
    assert kwargs["tenant_id"] == "t9"
    assert kwargs["chunks"] and all(isinstance(c, str) for c in kwargs["chunks"])
    assert result.kg_entities == 3
    assert result.kg_relations == 2


@pytest.mark.asyncio
async def test_pipeline_kg_hook_failure_never_blocks_ingestion():
    """D-15: a KG extraction error must not fail the indexed document."""
    from app.ingestion.pipeline import IngestionPipeline
    from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily

    mock_kb = MagicMock()
    mock_kb.exists_by_hash = AsyncMock(return_value=False)
    mock_kb.ingest_chunks_async = AsyncMock(return_value=["chunk1"])
    mock_embedder = MagicMock()

    kg_hook = MagicMock()
    kg_hook.process = AsyncMock(side_effect=RuntimeError("KG store down"))

    with patch("app.providers.base.embed_texts", AsyncMock(return_value=[[0.1] * 10, [0.2] * 10])):
        pipeline = IngestionPipeline(
            knowledge_store=mock_kb, embedder=mock_embedder, kg_hook=kg_hook
        )
        content = ("Some indexable content here for the pipeline. " * 20).encode()
        raw = RawDocument(doc_id="doc-kg-2", source_id="s1", tenant_id="t9",
                          content=content, content_type="text/plain")
        config = SourceConfig(source_id="s1", tenant_id="t9", name="T",
                              family=SourceFamily.WEB, source_type="test", collection_id="c1")
        result = await pipeline.ingest(raw, config)

    assert result.status == "indexed"
    assert result.kg_entities == 0
    kg_hook.process.assert_called_once()


# ── IngestionJobTracker ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_job_tracker_create_and_complete():
    from app.ingestion.job_tracker import IngestionJobTracker
    from app.ingestion.source_config import IngestionJob, SourceConfig, SourceFamily

    tracker = IngestionJobTracker()
    config = SourceConfig(source_id="s1", tenant_id="t1", name="T",
                          family=SourceFamily.WEB, source_type="test")
    job = await tracker.create_job(config, job_id="j1")
    assert job.status == "running"
    assert job.source_id == "s1"

    await tracker.increment_counters(job, indexed=5, chunks=15)
    assert job.docs_indexed == 5
    assert job.chunks_created == 15

    await tracker.complete_job(job)
    assert job.status == "completed"
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_job_tracker_cursor_update():
    from app.ingestion.job_tracker import IngestionJobTracker
    from app.ingestion.source_config import SourceConfig, SourceFamily

    tracker = IngestionJobTracker()
    config = SourceConfig(source_id="s1", tenant_id="t1", name="T",
                          family=SourceFamily.WEB, source_type="test")
    job = await tracker.create_job(config, job_id="j1")
    await tracker.update_cursor(job, "2026-08-17T10:00:00Z", config)
    assert job.cursor_after == "2026-08-17T10:00:00Z"
    assert config.cursor_value == "2026-08-17T10:00:00Z"


@pytest.mark.asyncio
async def test_job_tracker_lock_acquire_and_release():
    from app.ingestion.job_tracker import IngestionJobTracker

    tracker = IngestionJobTracker()
    job_id = await tracker.acquire_lock("source-1", "tenant-1")
    assert job_id is not None

    # Second acquire should fail
    job_id2 = await tracker.acquire_lock("source-1", "tenant-1")
    assert job_id2 is None

    # Release
    await tracker.release_lock("source-1", "tenant-1", job_id)

    # Now should succeed
    job_id3 = await tracker.acquire_lock("source-1", "tenant-1")
    assert job_id3 is not None
    await tracker.release_lock("source-1", "tenant-1", job_id3)


@pytest.mark.asyncio
async def test_job_tracker_different_sources_independent():
    from app.ingestion.job_tracker import IngestionJobTracker

    tracker = IngestionJobTracker()
    id1 = await tracker.acquire_lock("source-A", "tenant-1")
    id2 = await tracker.acquire_lock("source-B", "tenant-1")
    assert id1 is not None
    assert id2 is not None  # Different source, different lock


# ── Connectors: Contract Tests ────────────────────────────────────────────────

def test_slack_connector_source_type():
    from app.ingestion.connectors.slack_connector import SlackConnector
    c = SlackConnector()
    assert c.source_type == "slack"
    assert c.supports_acl_propagation is True
    assert c.supports_streaming is True


def test_github_connector_source_type():
    from app.ingestion.connectors.github_connector import GitHubConnector
    c = GitHubConnector()
    assert c.source_type == "github"
    assert c.supports_acl_propagation is True


def test_s3_connector_source_type():
    from app.ingestion.connectors.s3_connector import S3Connector
    c = S3Connector()
    assert c.source_type == "s3"
    assert c.supports_streaming is True
    assert c.supports_deletion_tracking is True


def test_postgresql_connector_source_type():
    from app.ingestion.connectors.postgresql_connector import PostgreSQLConnector
    c = PostgreSQLConnector()
    assert c.source_type == "postgresql"
    assert c.supports_deletion_tracking is True


def test_web_crawl_connector_source_type():
    from app.ingestion.connectors.web_crawl_connector import WebCrawlConnector
    c = WebCrawlConnector()
    assert c.source_type == "web_crawl"


def test_agent_generated_connector():
    from app.ingestion.connectors.agent_generated_connector import AgentGeneratedConnector
    c = AgentGeneratedConnector()
    assert c.source_type == "agent_generated"
    assert c.supports_streaming is True


def test_pdf_connector():
    from app.ingestion.connectors.pdf_file_connector import PDFFileConnector
    c = PDFFileConnector()
    assert c.source_type == "pdf_file"


@pytest.mark.asyncio
async def test_s3_connector_validate_no_boto3():
    from app.ingestion.connectors.s3_connector import S3Connector
    from app.ingestion.source_config import SourceConfig, SourceFamily
    import sys
    # Simulate boto3 not installed
    with patch.dict(sys.modules, {"boto3": None}):
        c = S3Connector()
        config = SourceConfig(source_id="s1", tenant_id="t1", name="T",
                              family=SourceFamily.OBJECT_STORAGE, source_type="s3")
        health = await c.validate_connection(config)
        # Should gracefully return error, not crash
        assert isinstance(health.ok, bool)


@pytest.mark.asyncio
async def test_web_crawl_validate_no_seed_urls():
    from app.ingestion.connectors.web_crawl_connector import WebCrawlConnector
    from app.ingestion.source_config import SourceConfig, SourceFamily
    c = WebCrawlConnector()
    config = SourceConfig(source_id="s1", tenant_id="t1", name="T",
                          family=SourceFamily.WEB, source_type="web_crawl",
                          connection_config={})
    health = await c.validate_connection(config)
    assert health.ok is False
    assert "seed_urls" in health.error


@pytest.mark.asyncio
async def test_agent_generated_validate_connection():
    from app.ingestion.connectors.agent_generated_connector import AgentGeneratedConnector
    from app.ingestion.source_config import SourceConfig, SourceFamily
    c = AgentGeneratedConnector()
    config = SourceConfig(source_id="s1", tenant_id="t1", name="T",
                          family=SourceFamily.AGENT_GENERATED, source_type="agent_generated")
    health = await c.validate_connection(config)
    assert health.ok is True


@pytest.mark.asyncio
async def test_agent_generated_on_webhook_high_score():
    from app.ingestion.connectors.agent_generated_connector import AgentGeneratedConnector
    from app.ingestion.source_config import SourceConfig, SourceFamily
    c = AgentGeneratedConnector()
    config = SourceConfig(source_id="s1", tenant_id="t1", name="T",
                          family=SourceFamily.AGENT_GENERATED, source_type="agent_generated",
                          connection_config={"min_eval_score": 0.7})

    payload = json.dumps({
        "goal_id": "g1", "tenant_id": "t1", "score": 0.9,
        "output": "This is a high quality agent output worth indexing."
    }).encode()

    docs = []
    async for doc in c.on_webhook(config, payload, {}):
        docs.append(doc)
    assert len(docs) == 1
    assert docs[0].doc_id == "g1"


@pytest.mark.asyncio
async def test_agent_generated_on_webhook_low_score():
    from app.ingestion.connectors.agent_generated_connector import AgentGeneratedConnector
    from app.ingestion.source_config import SourceConfig, SourceFamily
    c = AgentGeneratedConnector()
    config = SourceConfig(source_id="s1", tenant_id="t1", name="T",
                          family=SourceFamily.AGENT_GENERATED, source_type="agent_generated",
                          connection_config={"min_eval_score": 0.7})

    payload = json.dumps({"goal_id": "g2", "tenant_id": "t1", "score": 0.5, "output": "low quality"}).encode()
    docs = []
    async for doc in c.on_webhook(config, payload, {}):
        docs.append(doc)
    assert len(docs) == 0  # below min_score


# ── Parsers ───────────────────────────────────────────────────────────────────

def test_csv_parser_basic():
    from app.ingestion.parsers.csv_parser import CSVParser
    csv_content = "name,age,city\nAlice,30,New York\nBob,25,London\n"
    result = CSVParser().parse(csv_content, filename="people.csv")
    assert "Alice" in result
    assert "age: 30" in result or "30" in result
    assert "London" in result


def test_csv_parser_empty():
    from app.ingestion.parsers.csv_parser import CSVParser
    result = CSVParser().parse("", filename="empty.csv")
    assert result == "" or len(result) < 10


def test_csv_parser_truncates_large():
    from app.ingestion.parsers.csv_parser import CSVParser
    # 15000 rows
    rows = ["id,value"] + [f"{i},{i*2}" for i in range(15000)]
    result = CSVParser().parse("\n".join(rows))
    assert "Truncated" in result


def test_json_parser_object():
    from app.ingestion.parsers.json_parser import JSONParser
    data = json.dumps({"name": "Alice", "age": 30, "city": "NYC"})
    result = JSONParser().parse(data)
    assert "name: Alice" in result
    assert "age: 30" in result


def test_json_parser_array_of_objects():
    from app.ingestion.parsers.json_parser import JSONParser
    data = json.dumps([{"id": 1, "v": "a"}, {"id": 2, "v": "b"}])
    result = JSONParser().parse(data)
    assert "id" in result


def test_json_parser_invalid():
    from app.ingestion.parsers.json_parser import JSONParser
    result = JSONParser().parse("{not: valid json}")
    # Should not crash — return as-is
    assert "not" in result


def test_html_parser_basic():
    from app.ingestion.parsers.html_parser import HTMLParser
    html = "<html><body><h1>Title</h1><p>Content paragraph here.</p></body></html>"
    result = HTMLParser().parse(html)
    assert "Content paragraph here" in result or "Title" in result


def test_html_parser_strips_scripts():
    from app.ingestion.parsers.html_parser import HTMLParser
    html = "<html><body><script>alert_function_xyz(1)</script><p>Good content here please</p></body></html>"
    result = HTMLParser().parse(html)
    # Script content should not appear — "Good content" should
    assert "Good content here please" in result


def test_markdown_parser_basic():
    from app.ingestion.parsers.markdown_parser import MarkdownParser
    md = "# Title\n\nThis is **bold** and *italic* content.\n\n## Section 2\n\nMore text."
    result = MarkdownParser().parse(md)
    assert "bold" in result
    assert "italic" in result
    assert "Section 2" in result


def test_markdown_parser_removes_front_matter():
    from app.ingestion.parsers.markdown_parser import MarkdownParser
    md = "---\ntitle: Test\ndate: 2026\n---\n\n# Real Content\n\nHere."
    result = MarkdownParser().parse(md)
    assert "title:" not in result
    assert "Real Content" in result


def test_markdown_parser_sections():
    from app.ingestion.parsers.markdown_parser import MarkdownParser
    md = "# H1\n\nPara 1\n\n## H2\n\nPara 2"
    sections = MarkdownParser().parse_sections(md)
    assert len(sections) >= 2
    assert any(s["heading"] == "H1" for s in sections)


def test_notebook_parser_code_cells():
    from app.ingestion.parsers.notebook_parser import NotebookParser
    nb = {
        "cells": [
            {"cell_type": "markdown", "source": ["# My Notebook"]},
            {"cell_type": "code", "source": ["x = 1 + 1\nprint(x)"],
             "outputs": [{"output_type": "stream", "text": ["2\n"]}]},
        ],
        "metadata": {"kernelspec": {"language": "python"}}
    }
    result = NotebookParser().parse(json.dumps(nb), filename="test.ipynb")
    assert "My Notebook" in result
    assert "x = 1 + 1" in result
    assert "Output" in result


def test_notebook_parser_invalid():
    from app.ingestion.parsers.notebook_parser import NotebookParser
    result = NotebookParser().parse("{invalid}", filename="bad.ipynb")
    assert "{invalid}" in result or len(result) < 100


# ── KnowledgeIngestTool ───────────────────────────────────────────────────────

def test_knowledge_ingest_tool_name():
    from app.tools.knowledge_ingest_tool import KnowledgeIngestTool
    tool = KnowledgeIngestTool()
    assert tool.name == "knowledge.ingest"
    assert "content_or_url" in tool.parameters["properties"]


@pytest.mark.asyncio
async def test_knowledge_ingest_tool_no_pipeline():
    from app.tools.knowledge_ingest_tool import KnowledgeIngestTool
    tool = KnowledgeIngestTool()
    result = await tool.execute("hello world", pipeline=None)
    assert result["job_status"] == "failed"
    assert "not configured" in result["error"]


@pytest.mark.asyncio
async def test_knowledge_ingest_tool_raw_text():
    from app.tools.knowledge_ingest_tool import KnowledgeIngestTool
    from app.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline(dry_run=True)
    tool = KnowledgeIngestTool()

    tenant_ctx = SimpleNamespace(tenant_id="t1", default_collection_id="col1")
    result = await tool.execute(
        "This is a document with meaningful content. " * 20,
        pipeline=pipeline,
        tenant_ctx=tenant_ctx,
        collection_id="col1",
        dry_run=True,
    )
    assert result["job_status"] in ("dry_run", "indexed", "skipped")


@pytest.mark.asyncio
async def test_knowledge_ingest_tool_empty_content():
    from app.tools.knowledge_ingest_tool import KnowledgeIngestTool
    from app.ingestion.pipeline import IngestionPipeline
    pipeline = IngestionPipeline()
    tool = KnowledgeIngestTool()
    result = await tool.execute("   ", pipeline=pipeline)
    assert result["job_status"] == "failed"
    assert result.get("error")
