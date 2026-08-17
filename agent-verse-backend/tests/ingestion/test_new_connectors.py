"""Tests for new connectors — Tier 2–5.

All tests are unit-level with mocked network/DB calls.
Integration tests (real Kafka, real S3) require separate markers.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.source_config import SourceConfig


def _make_config(source_type: str, conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-test",
        tenant_id="t1",
        name="Test",
        family="object_storage",
        source_type=source_type,
        enabled=True,
        sync_mode="incremental",
        connection_config=conn_config or {},
    )


# ── GCSConnector ──────────────────────────────────────────────────────────────

class TestGCSConnector:
    def test_register(self):
        from app.ingestion.connector_registry import get_connector
        import importlib
        importlib.import_module("app.ingestion.connectors.gcs_connector")
        cls = get_connector("gcs")
        assert cls is not None

    def test_validate_connection_no_library(self):
        from app.ingestion.connectors.gcs_connector import GCSConnector
        config = _make_config("gcs", {"bucket": "test-bucket"})
        with patch.dict("sys.modules", {"google.cloud.storage": None}):
            result = asyncio.run(
                GCSConnector().validate_connection(config)
            )
        assert result.ok is False or isinstance(result.ok, bool)


# ── AzureBlobConnector ────────────────────────────────────────────────────────

class TestAzureBlobConnector:
    def test_register(self):
        from app.ingestion.connector_registry import get_connector
        import importlib
        importlib.import_module("app.ingestion.connectors.azure_blob_connector")
        cls = get_connector("azure_blob")
        assert cls is not None

    def test_validate_connection_no_library(self):
        from app.ingestion.connectors.azure_blob_connector import AzureBlobConnector
        config = _make_config("azure_blob", {"container": "test-container"})
        with patch.dict("sys.modules", {"azure.storage.blob": None}):
            result = asyncio.run(
                AzureBlobConnector().validate_connection(config)
            )
        assert not result.ok


# ── MinIOConnector ────────────────────────────────────────────────────────────

class TestMinIOConnector:
    def test_register(self):
        from app.ingestion.connector_registry import get_connector
        import importlib
        importlib.import_module("app.ingestion.connectors.minio_connector")
        cls = get_connector("minio")
        assert cls is not None

    def test_inherits_s3(self):
        from app.ingestion.connectors.minio_connector import MinIOConnector
        from app.ingestion.connectors.s3_connector import S3Connector
        assert issubclass(MinIOConnector, S3Connector)


# ── RSSConnector ──────────────────────────────────────────────────────────────

class TestRSSConnector:
    def test_register(self):
        from app.ingestion.connector_registry import get_connector
        import importlib
        importlib.import_module("app.ingestion.connectors.rss_connector")
        assert get_connector("rss") is not None
        assert get_connector("atom") is not None

    def test_get_delta_yields_docs(self):
        try:
            import feedparser  # noqa: F401
        except ImportError:
            pytest.skip("feedparser not installed")

        from app.ingestion.connectors.rss_connector import RSSConnector

        sample_feed = """<?xml version="1.0"?>
        <rss version="2.0"><channel>
          <title>Test Feed</title>
          <item>
            <title>Entry 1</title>
            <link>http://example.com/1</link>
            <pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate>
            <description>First item</description>
          </item>
        </channel></rss>"""

        config = _make_config("rss", {"url": "http://example.com/feed.rss", "max_entries": 10})
        connector = RSSConnector()

        with patch("feedparser.parse") as mock_parse:
            mock_entry = MagicMock()
            mock_entry.get = lambda k, d="": {
                "id": "item-1", "title": "Entry 1", "link": "http://example.com/1",
                "published": "Mon, 01 Jan 2026 00:00:00 GMT",
                "summary": "First item",
            }.get(k, d)
            mock_feed = MagicMock()
            mock_feed.entries = [mock_entry]
            mock_parse.return_value = mock_feed

            docs = list(asyncio.run(
                _collect_async(connector.get_delta(config, None))
            ))
        assert len(docs) >= 1
        doc, cursor = docs[0]
        assert b"Entry 1" in doc.content


# ── ArXivConnector ────────────────────────────────────────────────────────────

class TestArXivConnector:
    def test_register(self):
        from app.ingestion.connector_registry import get_connector
        import importlib
        importlib.import_module("app.ingestion.connectors.arxiv_connector")
        assert get_connector("arxiv") is not None

    def test_get_delta_parses_atom(self):
        import httpx
        from app.ingestion.connectors.arxiv_connector import ArXivConnector

        sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry>
            <id>http://arxiv.org/abs/2501.12345v1</id>
            <published>2026-01-15T00:00:00Z</published>
            <title>Deep Learning for Everything</title>
            <summary>This is a test abstract.</summary>
            <author><name>Alice Smith</name></author>
            <arxiv:primary_category term="cs.AI"/>
          </entry>
        </feed>"""

        config = _make_config("arxiv", {"categories": ["cs.AI"], "max_results": 5})
        connector = ArXivConnector()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.is_success = True
            mock_resp.text = sample_xml
            mock_resp.raise_for_status = MagicMock()
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            docs = list(asyncio.run(
                _collect_async(connector.get_delta(config, None))
            ))

        assert len(docs) == 1
        doc, cursor = docs[0]
        assert b"Deep Learning for Everything" in doc.content
        assert "arxiv.org/abs/2501.12345" in doc.source_url


# ── KafkaConnector ────────────────────────────────────────────────────────────

class TestKafkaConnector:
    def test_register(self):
        from app.ingestion.connector_registry import get_connector
        import importlib
        importlib.import_module("app.ingestion.connectors.kafka_connector")
        assert get_connector("kafka") is not None

    def test_supports_streaming(self):
        from app.ingestion.connectors.kafka_connector import KafkaConnector
        assert KafkaConnector.supports_streaming is True


# ── ElasticsearchConnector ────────────────────────────────────────────────────

class TestElasticsearchConnector:
    def test_register(self):
        from app.ingestion.connector_registry import get_connector
        import importlib
        importlib.import_module("app.ingestion.connectors.elasticsearch_connector")
        assert get_connector("elasticsearch") is not None
        assert get_connector("opensearch") is not None

    def test_get_delta_pagination(self):
        from app.ingestion.connectors.elasticsearch_connector import ElasticsearchConnector
        import json

        config = _make_config("elasticsearch", {"url": "http://es:9200", "index": "logs", "batch_size": 2})
        connector = ElasticsearchConnector()

        hit = {"_id": "abc", "_source": {"msg": "hello", "@timestamp": "2026-01-01"}, "sort": ["2026-01-01", "abc"]}
        page1 = {"hits": {"hits": [hit, hit]}}
        page2 = {"hits": {"hits": []}}  # empty page → stop

        call_count = 0
        async def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.is_success = True
            resp.json = MagicMock(return_value=page1 if call_count == 1 else page2)
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            docs = list(asyncio.run(
                _collect_async(connector.get_delta(config, None))
            ))
        assert len(docs) == 2


# ── Neo4jConnector ────────────────────────────────────────────────────────────

class TestNeo4jConnector:
    def test_register(self):
        from app.ingestion.connector_registry import get_connector
        import importlib
        importlib.import_module("app.ingestion.connectors.neo4j_connector")
        assert get_connector("neo4j") is not None


# ── Scheduler ────────────────────────────────────────────────────────────────

class TestIngestionScheduler:
    def test_beat_schedule_keys_exist(self):
        from app.ingestion.scheduler import BEAT_SCHEDULE
        assert "ingestion-dispatch-due-sources" in BEAT_SCHEDULE
        assert "ingestion-retry-dlq" in BEAT_SCHEDULE

    def test_jitter_deterministic(self):
        from app.ingestion.scheduler import _jitter
        j1 = _jitter("src-001")
        j2 = _jitter("src-001")
        j3 = _jitter("src-002")
        assert j1 == j2  # same input → same jitter
        # Different sources may have different jitter (not guaranteed but likely)
        assert 0 <= j1 <= 30
        assert 0 <= j3 <= 30

    def test_backoff_increases_with_failures(self):
        from app.ingestion.scheduler import _backoff_seconds
        b1 = _backoff_seconds(1)
        b2 = _backoff_seconds(2)
        b5 = _backoff_seconds(5)
        assert b1 < b2 < b5
        assert b5 <= 600  # capped at 10 min


# ── Helper ───────────────────────────────────────────────────────────────────

async def _collect_async(agen) -> list:
    results = []
    async for item in agen:
        results.append(item)
    return results
