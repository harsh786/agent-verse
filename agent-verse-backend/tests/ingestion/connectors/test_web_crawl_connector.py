"""WebCrawlConnector (P1d): a polite, bounded, incremental crawl of one site.

The crawl runs against a real local HTTP site (the real-world suite's
programmable fixture server on 127.0.0.1, operator-allowlisted for the egress
guard), so redirects, robots.txt, the size cap and charset handling are real.

Before P1d the crawler skipped every URL it had seen in any earlier sync (a
changed page was never read again), ignored ``respect_robots_txt`` and
``max_depth`` (only "> 1"), followed at most 20 links per page, dropped every
link with a query string or a fragment, decoded pages as UTF-8 regardless of
their charset, treated a canonical or duplicate page as another document, and
could not reconcile removed pages.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Iterator
from typing import Any

import pytest

from app.ingestion.connectors.web_crawl_connector import (
    WebCrawlConnector,
    normalize_url,
    page_doc_id,
)
from app.ingestion.base_connector import LiveListingUnavailableError
from app.ingestion.source_config import SourceConfig
from tests.ingestion._drain import content_docs, drain, failure_docs
from tests.real_world.fixture_server import FixtureServer

_TEXT = "Container yard operations manual paragraph with enough words to index. "


def _page(title: str, body: str = "", links: tuple[str, ...] = (), head: str = "") -> str:
    anchors = "".join(f'<a href="{href}">{href}</a> ' for href in links)
    return (f"<html><head><title>{title}</title>{head}</head><body>"
            f"<nav><a href='/'>Home</a></nav><main><h1>{title}</h1>"
            f"<p>{body or _TEXT * 3}</p><p>{anchors}</p></main></body></html>")


class Site:
    def __init__(self, server: FixtureServer) -> None:
        self.server = server
        self.base = f"http://127.0.0.1:{server.port}"

    def route(self, path: str, body: str | bytes = "", status: int = 200,
              headers: dict[str, str] | None = None, **kw: Any) -> None:
        data = body.encode() if isinstance(body, str) else body
        self.server.set_route({"path": path, "status": status, "headers": headers or {},
                               "body_b64": base64.b64encode(data).decode(), **kw})

    def html(self, path: str, title: str, *links: str, body: str = "", head: str = "") -> None:
        self.route(path, _page(title, body, links, head))

    def hits(self, prefix: str = "/") -> list[str]:
        return [h["path"] for h in self.server.site_hits(prefix)]

    def url(self, path: str) -> str:
        return f"{self.base}{path}"


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> Iterator[Site]:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "127.0.0.1")
    get_settings.cache_clear()
    server = FixtureServer(port=0, host="127.0.0.1").start()
    try:
        yield Site(server)
    finally:
        server.stop()
        get_settings.cache_clear()


def _config(site: Site, cursor: str = "", **cc: Any) -> SourceConfig:
    conn: dict[str, Any] = {"seed_urls": [site.url("/")], "crawl_delay_seconds": 0, **cc}
    return SourceConfig(source_id="src-web", tenant_id="t1", name="crawl", family="web",
                        source_type="web_crawl", connection_config=conn, cursor_value=cursor)


async def _crawl(site: Site, cursor: str = "", **cc: Any) -> tuple[list[Any], WebCrawlConnector]:
    connector = WebCrawlConnector()
    items, exc = await drain(connector.get_delta(_config(site, cursor, **cc), cursor or None))
    assert exc is None, exc
    return items, connector


def _urls(items: list[Any]) -> list[str]:
    return sorted(d.source_url for d in content_docs(items))


# ── normalisation ───────────────────────────────────────────────────────────


def test_normalize_url_drops_fragments_tracking_and_default_ports() -> None:
    assert normalize_url("HTTPS://Example.ORG:443/a/b?utm_source=x&b=2&a=1#top") == \
        "https://example.org/a/b?a=1&b=2"
    assert normalize_url("/x?fbclid=1", "http://h.example:80/dir/") == "http://h.example/x"
    assert normalize_url("../up", "https://h.example/a/b/") == "https://h.example/a/up"
    assert normalize_url("mailto:a@b.c") is None
    assert normalize_url("javascript:void(0)") is None
    assert normalize_url("http://[broken") is None


# ── crawl scope ─────────────────────────────────────────────────────────────


async def test_depth_page_limits_and_same_host(site: Site) -> None:
    site.html("/", "Home", "/a", "/b", "https://other.example/x", "http://localhost:9/z")
    site.html("/a", "A", "/a/deep")
    site.html("/b", "B")
    site.html("/a/deep", "Deep", "/a/deeper")
    site.html("/a/deeper", "Deeper")
    items, conn = await _crawl(site, max_depth=2)
    assert _urls(items) == [site.url(p) for p in ("/", "/a", "/b")]
    assert "/a/deep" not in site.hits()
    state = json.loads(conn.completed_cursor or "")
    assert state["complete"] is True and len(state["live"]) == 3

    site.server.site_log.clear()
    items, conn = await _crawl(site, max_depth=5, max_pages=2)
    assert len(content_docs(items)) == 2
    assert json.loads(conn.completed_cursor or "")["complete"] is False


async def test_more_than_twenty_links_per_page_are_followed(site: Site) -> None:
    paths = [f"/doc/{i}" for i in range(30)]
    site.html("/", "Index", *paths)
    for i, path in enumerate(paths):
        site.html(path, f"Doc {i}", body=f"Document number {i}. " + _TEXT * 2)
    items, _ = await _crawl(site, max_pages=50)
    assert len(content_docs(items)) == 31


async def test_query_variants_fragments_and_loops_are_one_page(site: Site) -> None:
    site.html("/", "Home", "/a?utm_source=news#top", "/a?ref=footer", "/a", "/b")
    site.html("/a", "A", "/b", "/")
    site.html("/b", "B", "/a", "/?utm_campaign=x")
    items, conn = await _crawl(site)
    assert _urls(items) == [site.url(p) for p in ("/", "/a", "/b")]
    assert site.hits("/a").count("/a") == 1
    assert json.loads(conn.completed_cursor or "")["complete"] is True


async def test_canonical_and_duplicate_pages_are_one_document(site: Site) -> None:
    site.html("/", "Home", "/article?id=7", "/print/7", "/b")
    canonical = '<link rel="canonical" href="/articles/7">'
    site.html("/article", "Article 7", head=canonical, body="Gate 4 closes at 22:00. " + _TEXT)
    site.route("/article?id=7", _page("Article 7", "Gate 4 closes at 22:00. " + _TEXT, (),
                                      canonical))
    site.route("/print/7", _page("Article 7", "Gate 4 closes at 22:00. " + _TEXT, (),
                                 canonical))
    site.html("/b", "Bulletin", "/copy", body="Weekly bulletin. " + _TEXT)
    site.html("/copy", "Bulletin", "/copy", body="Weekly bulletin. " + _TEXT)  # same text
    items, _ = await _crawl(site)
    docs = content_docs(items)
    article = [d for d in docs if "Gate 4" in d.content.decode()]
    assert len(article) == 1
    assert article[0].source_url == site.url("/articles/7")
    assert article[0].doc_id == page_doc_id(site.url("/articles/7"))
    # home, the article, the bulletin; /copy (same text as /b) is no second document
    assert sorted(d.source_url for d in docs) == [site.url(p) for p in ("/", "/articles/7", "/b")]


# ── robots.txt, meta robots, politeness ─────────────────────────────────────


async def test_robots_txt_is_honoured(site: Site, monkeypatch: pytest.MonkeyPatch) -> None:
    site.route("/robots.txt", "User-agent: *\nDisallow: /private/\nCrawl-delay: 2\n",
               headers={"Content-Type": "text/plain"})
    site.html("/", "Home", "/private/salaries", "/public")
    site.html("/private/salaries", "Salaries")
    site.html("/public", "Public")
    waits: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    items, _ = await _crawl(site)
    assert _urls(items) == [site.url("/"), site.url("/public")]
    assert "/private/salaries" not in site.hits()
    # Crawl-delay 2 s between requests to the host (robots, /, /public).
    assert len([w for w in waits if w > 1.0]) >= 2


async def test_unreadable_robots_txt_stops_the_crawl_honestly(site: Site) -> None:
    site.route("/robots.txt", "down", status=503)
    site.html("/", "Home")
    items, conn = await _crawl(site)
    (failed,) = failure_docs(items)
    assert "robots.txt" in failed.metadata["connector_failure"]
    assert failed.metadata["connector_failure_retryable"] is True
    assert "/" not in [h for h in site.hits() if h != "/robots.txt"]
    assert json.loads(conn.completed_cursor or "")["complete"] is False


async def test_meta_robots_noindex_and_nofollow(site: Site) -> None:
    site.html("/", "Home", "/hidden", "/nofollow")
    site.html("/hidden", "Hidden", "/via-hidden", head='<meta name="robots" content="noindex">')
    site.html("/via-hidden", "Via hidden")
    site.html("/nofollow", "No follow", "/never", head='<meta name="robots" content="nofollow">')
    site.html("/never", "Never")
    items, _ = await _crawl(site)
    assert _urls(items) == [site.url(p) for p in ("/", "/nofollow", "/via-hidden")]
    assert "/never" not in site.hits()


async def test_retry_after_is_waited_out_once(site: Site,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    site.route("/", _page("Home"), fail_first=1, fail_status=429,
               fail_headers={"Retry-After": "3"})
    waits: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    items, _ = await _crawl(site)
    assert _urls(items) == [site.url("/")]
    assert 3.0 in waits


# ── sitemap ─────────────────────────────────────────────────────────────────


async def test_sitemap_index_and_orphan_pages(site: Site) -> None:
    site.html("/", "Home")
    site.html("/orphan", "Orphan", body="Only listed in the sitemap. " + _TEXT)
    site.route("/sitemap-index.xml", (
        '<?xml version="1.0"?><sitemapindex><sitemap><loc>'
        f"{site.url('/sitemap-pages.xml')}</loc></sitemap></sitemapindex>"),
        headers={"Content-Type": "application/xml"})
    site.route("/sitemap-pages.xml", (
        f"<urlset><url><loc>{site.url('/orphan')}</loc></url>"
        "<url><loc>http://169.254.169.254/latest/meta-data/</loc></url></urlset>"),
        headers={"Content-Type": "application/xml"})
    items, _ = await _crawl(site, sitemap_url=site.url("/sitemap-index.xml"))
    assert _urls(items) == [site.url("/"), site.url("/orphan")]


# ── failures, redirects, SSRF ───────────────────────────────────────────────


async def test_failures_are_counted_and_retryable_ones_replayable(site: Site) -> None:
    site.html("/", "Home", "/gone", "/flaky")
    site.route("/gone", "gone", status=404)
    site.route("/flaky", "busy", status=503)
    items, conn = await _crawl(site, seed_urls=[site.url("/"), site.url("/missing-seed")])
    failures = {d.source_url: d for d in failure_docs(items)}
    # A dead LINK is the site's problem (logged); a dead SEED is a counted failure.
    assert set(failures) == {site.url("/missing-seed"), site.url("/flaky")}
    assert failures[site.url("/missing-seed")].metadata["connector_failure_retryable"] is False
    flaky = failures[site.url("/flaky")]
    assert flaky.metadata["connector_failure_retryable"] is True
    assert flaky.metadata["connector_replay"] == {"kind": "web_page", "url": site.url("/flaky")}
    assert json.loads(conn.completed_cursor or "")["complete"] is False

    site.html("/flaky", "Flaky", body="Back again. " + _TEXT)
    replayed = [d async for d in WebCrawlConnector().replay_event(
        _config(site), flaky.metadata["connector_replay"])]
    assert len(replayed) == 1 and "Back again." in replayed[0].content.decode()
    assert replayed[0].doc_id == flaky.doc_id


async def test_redirects_are_followed_and_internal_ones_refused(site: Site) -> None:
    site.html("/", "Home", "/old", "/evil")
    site.route("/old", "", status=301, headers={"Location": "/new"})
    site.html("/new", "New", body="Moved content. " + _TEXT)
    site.route("/evil", "", status=302,
               headers={"Location": "http://169.254.169.254/latest/meta-data/"})
    items, _ = await _crawl(site, seed_urls=[site.url("/"), site.url("/evil")])
    assert _urls(items) == [site.url("/"), site.url("/new")]
    (refused,) = failure_docs(items)
    assert refused.source_url == site.url("/evil")
    assert "blocked" in refused.metadata["connector_failure"]
    assert refused.metadata["connector_failure_retryable"] is False


async def test_a_blocked_seed_is_a_counted_failure() -> None:
    config = SourceConfig(source_id="s", tenant_id="t", name="n", family="web",
                          source_type="web_crawl",
                          connection_config={"seed_urls": ["http://169.254.169.254/x",
                                                           "not-a-url"]})
    items, exc = await drain(WebCrawlConnector().get_delta(config, None))
    assert exc is None
    assert len(failure_docs(items)) == 2
    assert all(not d.metadata["connector_failure_retryable"] for d in failure_docs(items))


async def test_an_oversized_page_is_refused(site: Site) -> None:
    site.html("/", "Home", "/huge")
    site.route("/huge", "<p>" + "x" * 5000 + "</p>")
    items, _ = await _crawl(site, max_page_bytes=2048, seed_urls=[site.url("/"),
                                                                    site.url("/huge")])
    (failed,) = failure_docs(items)
    assert failed.source_url == site.url("/huge")
    assert "limit" in failed.metadata["connector_failure"]


# ── content kinds and charset ───────────────────────────────────────────────


async def test_charset_and_linked_documents(site: Site) -> None:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, "Reefer plug-in per day: 2,450 INR")
    site.route("/", _page("Accueil", "Café crème brûlée à 12 €. " + _TEXT, ("/tariff.pdf",),
                          '<meta charset="windows-1252">').encode("cp1252"),
               headers={"Content-Type": "text/html"})
    site.route("/tariff.pdf", bytes(pdf.output()),
               headers={"Content-Type": "application/octet-stream"})
    items, _ = await _crawl(site)
    docs = {d.source_url: d for d in content_docs(items)}
    assert "Café crème brûlée à 12 €." in docs[site.url("/")].content.decode()
    assert docs[site.url("/")].content_type.startswith("text/markdown")
    pdf_doc = docs[site.url("/tariff.pdf")]
    assert pdf_doc.content.startswith(b"%PDF-")
    assert pdf_doc.content_type == "application/pdf"


# ── incremental re-crawl and reconciliation ─────────────────────────────────


async def test_every_sync_reads_the_site_again(site: Site) -> None:
    site.html("/", "Home", "/news")
    site.html("/news", "News", body="Version one of the news. " + _TEXT)
    _first, conn = await _crawl(site)
    cursor = conn.completed_cursor or ""
    site.html("/news", "News", body="Version two of the news. " + _TEXT)
    second, conn2 = await _crawl(site, cursor=cursor)
    news = next(d for d in content_docs(second) if d.source_url == site.url("/news"))
    assert "Version two" in news.content.decode()
    # A legacy (v1) cursor — the list of seen URL hashes — is not a skip list either.
    legacy = json.dumps([page_doc_id(site.url("/"))[6:]])
    third, _ = await _crawl(site, cursor=legacy)
    assert len(content_docs(third)) == 2


async def test_live_listing_comes_from_the_last_complete_crawl(site: Site) -> None:
    site.html("/", "Home", "/a", "/b")
    site.html("/a", "A")
    site.html("/b", "B")
    _items, conn = await _crawl(site)
    connector = WebCrawlConnector()
    config = _config(site, conn.completed_cursor or "")
    live = sorted([d async for d in connector.iter_live_doc_ids(config)])
    assert live == sorted(page_doc_id(site.url(p)) for p in ("/", "/a", "/b"))
    assert connector.manages_doc_id(live[0])

    site.route("/b", "gone", status=404)
    _items, conn = await _crawl(site, cursor=conn.completed_cursor or "")
    live = sorted([d async for d in connector.iter_live_doc_ids(
        _config(site, conn.completed_cursor or ""))])
    assert page_doc_id(site.url("/b")) not in live and len(live) == 2

    for cursor in ("", json.dumps(["abc"]), json.dumps({"v": 2, "complete": False})):
        with pytest.raises(LiveListingUnavailableError):
            [d async for d in connector.iter_live_doc_ids(_config(site, cursor))]


class TestValidateConnection:
    async def test_no_seed_urls(self) -> None:
        config = SourceConfig(source_id="s", tenant_id="t", name="n", family="web",
                              source_type="web_crawl", connection_config={})
        result = await WebCrawlConnector().validate_connection(config)
        assert result.ok is False
        assert "seed_urls" in result.error

    async def test_reachable_and_failing(self, site: Site) -> None:
        site.html("/", "Home")
        ok = await WebCrawlConnector().validate_connection(_config(site))
        assert ok.ok is True and ok.metadata["status"] == 200
        site.route("/", "nope", status=404)
        bad = await WebCrawlConnector().validate_connection(_config(site))
        assert bad.ok is False and "404" in bad.error


class TestExtractHelpers:
    def test_extract_text_strips_tags(self) -> None:
        text = WebCrawlConnector._extract_text("<html><body><p>Hello   world</p></body></html>")
        assert "Hello" in text and "world" in text and "<p>" not in text

    def test_extract_title(self) -> None:
        assert WebCrawlConnector._extract_title("<title> My Page </title>") == "My Page"
        assert WebCrawlConnector._extract_title("<html></html>") == ""

    def test_extract_links_same_host_normalised(self) -> None:
        html = ('<a href="/page2#x">p2</a><a href="/page2">again</a>'
                '<a href="https://other.com/x">other</a><a href="mailto:a@b.com">mail</a>'
                '<a href="/q?b=2&a=1&utm_medium=email">q</a>')
        links = WebCrawlConnector._extract_links(html, "https://example.com/")
        assert links == ["https://example.com/page2", "https://example.com/q?a=1&b=2"]


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

    def test_page_chrome_is_dropped(self):
        """P2-5: crawled pages lose nav/header/footer like uploads and /ingest/url."""
        html = (
            "<html><body><header><nav><a href='/'>Home</a> <a href='/x'>Index</a></nav>"
            "</header><main><article><h1>Zen</h1><p>Simple is better than complex.</p>"
            "</article></main><footer>Copyright footer</footer></body></html>"
        )
        text = WebCrawlConnector._extract_text(html)
        assert "Simple is better than complex." in text
        assert "Index" not in text
        assert "Copyright footer" not in text

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
