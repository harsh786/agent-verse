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

    async def test_redirect_loop_is_skipped_gracefully(self):
        # httpx raises TooManyRedirects when follow_redirects=True hits a
        # cycle; the per-URL try/except must swallow it and keep crawling.
        import httpx as real_httpx

        client = _fake_client(AsyncMock(side_effect=real_httpx.TooManyRedirects("loop")))
        config = _make_config({"seed_urls": ["https://example.com/loop"], "crawl_delay_seconds": 0})
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(WebCrawlConnector().get_delta(config, None))
        assert docs == []

    async def test_invalid_utf8_bytes_decoded_with_replacement_not_fatal(self):
        # Response body with invalid UTF-8 byte sequences must not crash the
        # crawl — get_delta decodes with errors="replace".
        html = (
            b"<html><head><title>T</title></head><body>"
            + b"Valid text content here. " * 10
            + b"\xff\xfe invalid bytes here \xff"
            + b"</body></html>"
        )
        resp = MagicMock(status_code=200, content=html, headers={})
        client = _fake_client(AsyncMock(return_value=resp))
        config = _make_config({"seed_urls": ["https://example.com/"], "crawl_delay_seconds": 0})
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(WebCrawlConnector().get_delta(config, None))
        assert len(docs) == 1
        assert "Valid text content" in docs[0][0].content.decode()

    async def test_malformed_url_in_seed_list_is_skipped_not_fatal(self):
        # A malformed seed URL fails at request time; the crawler must not
        # let one bad URL abort the whole run.
        good_html = (
            b"<html><head><title>Good</title></head><body>"
            + b"Good content here for the page. " * 10
            + b"</body></html>"
        )
        responses = iter(
            [
                OSError("Invalid URL"),
                MagicMock(status_code=200, content=good_html, headers={}),
            ]
        )

        async def get(*a, **kw):
            r = next(responses)
            if isinstance(r, Exception):
                raise r
            return r

        client = _fake_client(get)
        config = _make_config(
            {
                "seed_urls": ["not-a-valid-url", "https://example.com/good"],
                "crawl_delay_seconds": 0,
            }
        )
        with patch("httpx.AsyncClient", return_value=client):
            docs = await _collect(WebCrawlConnector().get_delta(config, None))
        assert len(docs) == 1
        assert docs[0][0].title == "Good"


class TestExtractTextEdgeCases:
    def test_malformed_html_unclosed_tags_does_not_raise(self):
        html = "<html><body><div><p>Unclosed paragraph<div>More text</body>"
        text = WebCrawlConnector._extract_text(html)
        assert "Unclosed paragraph" in text
        assert "More text" in text

    def test_extremely_deep_dom_nesting_does_not_raise(self):
        depth = 500
        html = "<div>" * depth + "deep content here" + "</div>" * depth
        text = WebCrawlConnector._extract_text(html)
        assert "deep content here" in text

    def test_script_and_style_content_stripped_not_leaked(self):
        html = (
            "<html><body><script>var token = 'super-secret';</script>"
            "<style>.x { color: red; }</style>"
            "<p>Visible article body.</p></body></html>"
        )
        text = WebCrawlConnector._extract_text(html)
        assert "Visible article body" in text
        assert "super-secret" not in text
        assert "color: red" not in text

    def test_non_ascii_encoding_preserved(self):
        html = "<html><body><p>Café naïve 日本語 emoji 🎉</p></body></html>"
        text = WebCrawlConnector._extract_text(html)
        assert "Café" in text
        assert "日本語" in text
        assert "🎉" in text



class TestExtractLinksEdgeCases:
    def test_malformed_href_does_not_raise(self):
        html = '<a href="http://[invalid">bad</a><a href="/ok">ok</a>'
        links = WebCrawlConnector._extract_links(html, "https://example.com/")
        assert "https://example.com/ok" in links
