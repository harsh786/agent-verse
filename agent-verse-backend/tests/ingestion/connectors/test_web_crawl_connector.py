"""Tests for WebCrawlConnector — sitemap-free seed crawl with link discovery.

trafilatura is not installed in this environment, so ``_extract_text``
naturally exercises its regex-based fallback branch (no extra mocking
needed for that path). httpx is real and installed, so we patch
``httpx.AsyncClient`` per-test.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.ingestion.connectors.web_crawl_connector import WebCrawlConnector
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-web",
        tenant_id="t1",
        name="Test Crawl",
        family="web",
        source_type="web_crawl",
        connection_config=conn_config or {},
    )


async def _collect(agen) -> list:
    out = []
    async for item in agen:
        out.append(item)
    return out


def _fake_client(get_impl):
    client = AsyncMock()
    client.get = get_impl
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


class TestExtractHelpers:
    def test_extract_text_fallback_strips_tags(self):
        html = "<html><body><p>Hello   world</p></body></html>"
        text = WebCrawlConnector._extract_text(html)
        assert "Hello" in text and "world" in text
        assert "<p>" not in text

    def test_extract_title(self):
        html = "<html><head><title> My Page </title></head></html>"
        assert WebCrawlConnector._extract_title(html) == "My Page"

    def test_extract_title_missing(self):
        assert WebCrawlConnector._extract_title("<html></html>") == ""

    def test_extract_links_same_domain_only(self):
        html = (
            '<a href="/page2">p2</a>'
            '<a href="https://other.com/x">other</a>'
            '<a href="mailto:a@b.com">mail</a>'
        )
        links = WebCrawlConnector._extract_links(html, "https://example.com/")
        assert "https://example.com/page2" in links
        assert not any("other.com" in link for link in links)

    def test_extract_links_deduplicates(self):
        html = '<a href="/x">1</a><a href="/x">2</a>'
        links = WebCrawlConnector._extract_links(html, "https://example.com/")
        assert links == ["https://example.com/x"]


class TestValidateConnection:
    async def test_no_seed_urls(self):
        config = _make_config({})
        result = await WebCrawlConnector().validate_connection(config)
        assert result.ok is False
        assert "seed_urls" in result.error

    async def test_success(self):
        resp = MagicMock(status_code=200)
        client = _fake_client(AsyncMock(return_value=resp))
        config = _make_config({"seed_urls": ["https://example.com/"]})
        with patch("httpx.AsyncClient", return_value=client):
            result = await WebCrawlConnector().validate_connection(config)
        assert result.ok is True
        assert result.metadata["status"] == 200

    async def test_http_error_status(self):
        resp = MagicMock(status_code=404)
        client = _fake_client(AsyncMock(return_value=resp))
        config = _make_config({"seed_urls": ["https://example.com/missing"]})
        with patch("httpx.AsyncClient", return_value=client):
            result = await WebCrawlConnector().validate_connection(config)
        assert result.ok is False
        assert "404" in result.error

    async def test_exception(self):
        client = _fake_client(AsyncMock(side_effect=OSError("dns fail")))
        config = _make_config({"seed_urls": ["https://example.com/"]})
        with patch("httpx.AsyncClient", return_value=client):
            result = await WebCrawlConnector().validate_connection(config)
        assert result.ok is False
        assert "dns fail" in result.error


class TestGetDelta:
    async def test_no_httpx_yields_nothing(self):
        config = _make_config({"seed_urls": ["https://example.com/"]})
        with patch.dict("sys.modules", {"httpx": None}):
            docs = await _collect(WebCrawlConnector().get_delta(config, None))
        assert docs == []

    async def test_crawls_seed_and_extracts_doc(self):
        html = (
            b"<html><head><title>Home Page</title></head><body>"
            + b"Hello world content. " * 20
            + b'<a href="/page2">Page 2</a></body></html>'
        )
        resp = MagicMock(status_code=200, content=html, headers={"content-type": "text/html"})
        client = _fake_client(AsyncMock(return_value=resp))
        config = _make_config(
            {
                "seed_urls": ["https://example.com/"],
                "max_pages": 1,
                "max_depth": 1,
                "crawl_delay_seconds": 0,
            }
        )
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(WebCrawlConnector().get_delta(config, None))

        assert len(docs) == 1
        doc, cursor = docs[0]
        assert doc.title == "Home Page"
        assert "Hello world" in doc.content.decode()
        assert doc.source_url == "https://example.com/"
        assert doc.metadata["original_url"] == "https://example.com/"
        assert len(json.loads(cursor)) == 1

    async def test_skips_already_seen_url(self):
        html = b"<html><head><title>T</title></head><body>" + b"x" * 200 + b"</body></html>"
        resp = MagicMock(status_code=200, content=html, headers={})
        client = _fake_client(AsyncMock(return_value=resp))
        url = "https://example.com/"
        import hashlib

        seen_cursor = json.dumps([hashlib.md5(url.encode()).hexdigest()])
        config = _make_config({"seed_urls": [url], "crawl_delay_seconds": 0})
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(WebCrawlConnector().get_delta(config, seen_cursor))
        assert docs == []

    async def test_skips_short_content(self):
        html = b"<html><body>too short</body></html>"
        resp = MagicMock(status_code=200, content=html, headers={})
        client = _fake_client(AsyncMock(return_value=resp))
        config = _make_config({"seed_urls": ["https://example.com/"], "crawl_delay_seconds": 0})
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(WebCrawlConnector().get_delta(config, None))
        assert docs == []

    async def test_skips_error_status_pages(self):
        resp = MagicMock(status_code=500, content=b"", headers={})
        client = _fake_client(AsyncMock(return_value=resp))
        config = _make_config({"seed_urls": ["https://example.com/broken"], "crawl_delay_seconds": 0})
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(WebCrawlConnector().get_delta(config, None))
        assert docs == []

    async def test_fetch_exception_is_skipped(self):
        client = _fake_client(AsyncMock(side_effect=OSError("timeout")))
        config = _make_config({"seed_urls": ["https://example.com/"], "crawl_delay_seconds": 0})
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(WebCrawlConnector().get_delta(config, None))
        assert docs == []

    async def test_exclude_pattern_filters_url(self):
        html = b"<html><body>" + b"x" * 200 + b"</body></html>"
        resp = MagicMock(status_code=200, content=html, headers={})
        client = _fake_client(AsyncMock(return_value=resp))
        config = _make_config(
            {
                "seed_urls": ["https://example.com/admin"],
                "exclude_url_pattern": "admin",
                "crawl_delay_seconds": 0,
            }
        )
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(WebCrawlConnector().get_delta(config, None))
        assert docs == []
        client.get.assert_not_called()

    async def test_discovers_links_when_depth_allows(self):
        page1 = (
            b"<html><head><title>Home</title></head><body>"
            + b"Hello world content. " * 20
            + b'<a href="/page2">Page 2</a></body></html>'
        )
        page2 = (
            b"<html><head><title>Second</title></head><body>"
            + b"More content here too. " * 20
            + b"</body></html>"
        )
        get_mock = AsyncMock(
            side_effect=[
                MagicMock(status_code=200, content=page1, headers={}),
                MagicMock(status_code=200, content=page2, headers={}),
            ]
        )
        client = _fake_client(get_mock)
        config = _make_config(
            {
                "seed_urls": ["https://example.com/"],
                "max_pages": 5,
                "max_depth": 2,
                "crawl_delay_seconds": 0,
            }
        )
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(WebCrawlConnector().get_delta(config, None))

        assert len(docs) == 2
        titles = {d.title for d, _c in docs}
        assert titles == {"Home", "Second"}

    async def test_include_pattern_filters_url(self):
        html = b"<html><body>" + b"x" * 200 + b"</body></html>"
        resp = MagicMock(status_code=200, content=html, headers={})
        client = _fake_client(AsyncMock(return_value=resp))
        config = _make_config(
            {
                "seed_urls": ["https://example.com/other"],
                "include_url_pattern": "/blog/",
                "crawl_delay_seconds": 0,
            }
        )
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(WebCrawlConnector().get_delta(config, None))
        assert docs == []
