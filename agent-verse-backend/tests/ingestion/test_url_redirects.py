"""USR-5: moved URLs are followed safely, and a permanent move is surfaced.

URL connectors fetched without following redirects, so a moved file or feed was
a failure ("Redirect response '301 Moved Permanently'") and the Source had to be
fixed by hand. Now redirects are followed with every hop re-validated by the
SSRF guard and a hop limit; the final URL is recorded on the document; a
permanent redirect (301/308) is surfaced on the sync job and the new URL is
recorded on the Source. A redirect to an internal address is refused and never
requested.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.ingestion.source_config import (
    CONNECTOR_FAILURE_KEY,
    CONNECTOR_MOVED_KEY,
    SourceConfig,
    SourceFamily,
)

pytestmark = pytest.mark.asyncio

OLD = "https://example.com/old/handbook.pdf"
NEW = "https://example.com/new/handbook.pdf"


def _site(routes: dict[str, tuple[int, str]], seen: list[str]) -> Callable[..., Any]:
    """``routes``: url -> (status, location-or-body)."""

    async def _send(self: httpx.AsyncClient, request: httpx.Request, **_: Any) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        status, value = routes.get(url, (404, "missing"))
        if 300 <= status < 400:
            return httpx.Response(status, headers={"location": value}, request=request)
        return httpx.Response(status, content=value.encode(), request=request)

    return _send


def _config(source_type: str, cc: dict[str, Any]) -> SourceConfig:
    return SourceConfig(
        source_id="src-redir", tenant_id="t-redir", name="n", family=SourceFamily.WEB,
        source_type=source_type, connection_config=cc, collection_id="col",
    )


async def _pdf_docs(routes: dict[str, tuple[int, str]], seen: list[str]) -> list[Any]:
    from app.ingestion.connectors.pdf_file_connector import PDFFileConnector

    with patch.object(httpx.AsyncClient, "send", _site(routes, seen)):
        return [d async for d, _ in PDFFileConnector().get_delta(_config("pdf_file", {"urls": [OLD]}), None)]


@pytest.mark.parametrize("status", [301, 308])
async def test_permanent_redirect_is_followed_and_the_new_url_surfaced(status: int) -> None:
    seen: list[str] = []
    (doc,) = await _pdf_docs({OLD: (status, NEW), NEW: (200, "%PDF-1.4 moved")}, seen)
    assert CONNECTOR_FAILURE_KEY not in doc.metadata
    assert doc.content == b"%PDF-1.4 moved"
    assert doc.metadata["final_url"] == NEW
    assert doc.metadata[CONNECTOR_MOVED_KEY] == {"from": OLD, "to": NEW, "status": status}
    assert seen == [OLD, NEW]


@pytest.mark.parametrize("status", [302, 303, 307])
async def test_temporary_redirect_is_followed_without_a_move_notice(status: int) -> None:
    seen: list[str] = []
    (doc,) = await _pdf_docs({OLD: (status, NEW), NEW: (200, "%PDF-1.4 temp")}, seen)
    assert doc.content == b"%PDF-1.4 temp"
    assert doc.metadata["final_url"] == NEW
    assert CONNECTOR_MOVED_KEY not in doc.metadata


async def test_relative_location_and_a_chain_record_the_final_url() -> None:
    seen: list[str] = []
    final = "https://other.com/c.pdf"
    (doc,) = await _pdf_docs(
        {
            OLD: (301, "/mid/b.pdf"),
            "https://example.com/mid/b.pdf": (301, final),
            final: (200, "%PDF chain"),
        },
        seen,
    )
    assert doc.metadata["final_url"] == final
    assert doc.metadata[CONNECTOR_MOVED_KEY]["to"] == final


@pytest.mark.parametrize(
    "target",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:169.254.169.254]/",
        "http://127.0.0.1:8080/admin",
        "http://10.0.0.7/internal.pdf",
        "http://metadata.google.internal/computeMetadata/v1/",
    ],
)
async def test_redirect_to_an_internal_address_is_refused_and_never_requested(
    target: str,
) -> None:
    seen: list[str] = []
    (doc,) = await _pdf_docs({OLD: (302, target)}, seen)
    assert seen == [OLD]  # the internal target was never requested
    reason = doc.metadata[CONNECTOR_FAILURE_KEY]
    assert "redirect" in reason.lower() and "blocked" in reason.lower(), reason
    assert doc.metadata["connector_failure_retryable"] is False
    assert doc.content == b""


async def test_the_hop_limit_stops_a_redirect_loop() -> None:
    seen: list[str] = []
    other = "https://example.com/loop.pdf"
    (doc,) = await _pdf_docs({OLD: (302, other), other: (302, OLD)}, seen)
    reason = doc.metadata[CONNECTOR_FAILURE_KEY]
    assert "redirect" in reason.lower(), reason
    assert len(seen) <= 6  # 1 request + at most 5 hops


async def test_http_connector_follows_a_moved_endpoint() -> None:
    from app.ingestion.connectors.http_connector import HttpApiConnector

    old, new = "https://93.184.216.34/api/v1/items", "https://93.184.216.34/api/v2/items"
    seen: list[str] = []
    routes = {old: (308, new), new: (200, '[{"id": 1, "title": "a"}]')}
    with patch.object(httpx.AsyncClient, "send", _site(routes, seen)):
        docs = [d async for d, _ in HttpApiConnector().get_delta(_config("http", {"url": old}), None)]
    (doc,) = docs
    assert doc.metadata["final_url"] == new
    assert doc.metadata[CONNECTOR_MOVED_KEY] == {"from": old, "to": new, "status": 308}


async def test_web_crawl_seed_redirect_is_followed_and_recorded() -> None:
    from app.ingestion.connectors.web_crawl_connector import WebCrawlConnector

    old, new = "https://example.com/docs", "https://example.com/handbook/"
    html = "<html><title>Handbook</title><body>" + "Useful words here. " * 20 + "</body></html>"
    seen: list[str] = []
    routes = {old: (301, new), new: (200, html)}
    cfg = _config("web_crawl", {"seed_urls": [old], "crawl_delay_seconds": 0, "max_depth": 1})
    with patch.object(httpx.AsyncClient, "send", _site(routes, seen)):
        docs = [d async for d, _ in WebCrawlConnector().get_delta(cfg, None)]
    (doc,) = docs
    assert doc.title == "Handbook"
    assert doc.metadata["final_url"] == new
    assert doc.metadata[CONNECTOR_MOVED_KEY]["to"] == new


async def test_web_crawl_redirect_to_internal_is_refused() -> None:
    from app.ingestion.connectors.web_crawl_connector import WebCrawlConnector

    old = "https://example.com/docs"
    seen: list[str] = []
    routes = {old: (302, "http://169.254.169.254/latest/meta-data/iam/")}
    cfg = _config("web_crawl", {"seed_urls": [old], "crawl_delay_seconds": 0})
    with patch.object(httpx.AsyncClient, "send", _site(routes, seen)):
        docs = [d async for d, _ in WebCrawlConnector().get_delta(cfg, None)]
    # robots.txt (404 here) and the seed; the redirect target is never requested.
    assert seen == ["https://example.com/robots.txt", old]
    (doc,) = docs
    assert "blocked" in doc.metadata[CONNECTOR_FAILURE_KEY].lower()


async def test_sync_surfaces_a_permanent_move_and_records_the_new_url() -> None:
    from tests.ingestion.test_sync_failure_never_completed import _sync

    old, new = "https://93.184.216.34/api/v1/items", "https://93.184.216.34/api/v2/items"
    seen: list[str] = []
    routes = {old: (301, new), new: (200, '[{"id": 1, "title": "a"}]')}
    with patch.object(httpx.AsyncClient, "send", _site(routes, seen)):
        job, store = await _sync("http", {"url": old})
    assert job.status == "completed" and job.docs_failed == 0
    assert job.docs_indexed + job.docs_skipped == 1  # the record was read (dry-run pipeline)
    assert "moved permanently" in job.error_message.lower()
    assert old in job.error_message and new in job.error_message
    # The new URL is recorded on the Source (offered for the update); the
    # configured URL itself is left as the tenant set it.
    assert store.config.connection_config["url"] == old
    assert store.config.connection_config["moved_permanently"] == {old: new}
