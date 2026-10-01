"""WEB-FORM-TYPES: a web_crawl Source created with the Sources UI payload runs.

The Web form keyed its crawler/sitemap fields off source types that do not
exist (``web_crawler`` / ``sitemap``; the real type is ``web_crawl``) and sent
the start URLs as a ``urls`` string, while the connector reads a ``seed_urls``
list — so every web_crawl Source created in the UI failed with "No seed_urls
configured". This drives the payload the form now sends (and the one older
builds sent) through the Sources API and the worker body against a real local
site, through the SSRF-pinned egress client (127.0.0.1 operator-allowlisted).
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from typing import Any

import pytest

import app.api.ingestion as ingestion_mod
from tests.ingestion.sources_harness import SourcesHarness

_BODY = "<p>" + "Knowledge base article text that is long enough to index. " * 4 + "</p>"
_PAGES = {
    "/": f"<html><title>Home</title><body>{_BODY}<a href='/guide'>guide</a></body></html>",
    "/guide": f"<html><title>Guide</title><body>{_BODY}<a href='/admin/x'>a</a></body></html>",
    "/admin/x": f"<html><title>Admin</title><body>{_BODY}</body></html>",
    "/orphan": f"<html><title>Orphan</title><body>{_BODY}</body></html>",
}


class _Site(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/sitemap.xml":
            port = self.server.server_address[1]
            body = (
                '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"<url><loc>http://127.0.0.1:{port}/orphan</loc></url>"
                "<url><loc>http://169.254.169.254/latest/meta-data/</loc></url>"
                "</urlset>"
            ).encode()
            ctype = "application/xml"
        elif self.path in _PAGES:
            body, ctype = _PAGES[self.path].encode(), "text/html"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    from app.core.config import get_settings

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "127.0.0.1")
    get_settings.cache_clear()
    ingestion_mod._SOURCES.clear()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        ingestion_mod._SOURCES.clear()
        get_settings.cache_clear()


def _harness() -> SourcesHarness:
    return SourcesHarness(family="web", source_type="web_crawl")


async def test_ui_payload_creates_a_source_that_validates_and_syncs(site: str) -> None:
    h = _harness()
    # Exactly what WebForm now submits for web_crawl.
    source = h.create(
        {
            "seed_urls": [f"{site}/"],
            "sitemap_url": f"{site}/sitemap.xml",
            "max_depth": 3,
            "max_pages": 20,
            "exclude_url_pattern": "/admin/",
            "crawl_delay_seconds": 0,
        }
    )

    health = h.health(source["source_id"])
    assert health["ok"] is True, health

    result, pipeline = await h.sync(source["source_id"])

    titles = sorted(d.title for d in pipeline.docs)
    # Seed + discovered link + sitemap entry; the excluded path and the
    # metadata URL from the sitemap are not fetched.
    assert titles == ["Guide", "Home", "Orphan"], titles
    assert result.get("docs_indexed") == 3, result


async def test_older_ui_payload_with_a_urls_string_still_runs(site: str) -> None:
    h = _harness()
    source = h.create({"urls": f"{site}/orphan\n", "crawl_delay_seconds": 0, "max_depth": 1})

    assert h.health(source["source_id"])["ok"] is True
    result, pipeline = await h.sync(source["source_id"])
    assert [d.title for d in pipeline.docs] == ["Orphan"]
    assert result.get("docs_indexed") == 1, result


async def test_no_urls_is_an_honest_error(site: str) -> None:
    h = _harness()
    source = h.create({"crawl_delay_seconds": 0})
    health = h.health(source["source_id"])
    assert health["ok"] is False
    assert "seed" in health["error"].lower()
