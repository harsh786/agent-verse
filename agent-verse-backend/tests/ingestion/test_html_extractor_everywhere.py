"""P1d-10: every ingestion path reads HTML with the P1a extractor (bbcff5010).

P2 found (``_new_findings``) that several paths still stripped tags with a
regex — keeping ``<script>`` / ``<style>`` bodies, navigation and footers, and
flattening headings, lists and table rows into one line: the parser registry's
HTML parser (connector WEB_PAGE / HTML documents parsed from text), the
orchestrator's HTML chunk fallback, the Zendesk and Confluence connectors, the
Confluence ingestor, the e-mail parser (HTML parts), the RPA page fetch and the
web-augmented RAG pattern's page text.
"""

from __future__ import annotations

from typing import Any

import pytest

PAGE = (
    "<div class='page'><nav class='breadcrumbs'><a href='/'>Help Center</a> &gt; "
    "<a href='/s'>Shipping</a></nav>"
    "<style>.panel{border:1px solid #ccc}</style><script>trackView('kb-42');</script>"
    "<h2>Reefer plug-in</h2><p>Plug-in costs <b>2,450 INR</b> per day &amp; box.</p>"
    "<ul><li>Book 48 h ahead</li><li>Pre-cool to -18 C</li></ul>"
    "<table><tr><th>Yard</th><th>Plugs</th></tr><tr><td>Hosur</td><td>120</td></tr></table>"
    "<footer>Was this article helpful? Subscribe to our newsletter</footer></div>"
)
KEEP = ("Plug-in costs 2,450 INR per day & box.", "- Book 48 h ahead", "Yard: Hosur; Plugs: 120")
DROP = ("trackView", "border:1px", "Subscribe to our newsletter", "Help Center >")


def _check(text: str) -> None:
    for needle in KEEP:
        assert needle in text, (needle, text)
    for needle in DROP:
        assert needle not in text, (needle, text)
    assert "<" not in text.replace("&lt;", "")


def test_parser_registry_html() -> None:
    from app.ingestion.content_classifier import ContentType
    from app.ingestion.parser_registry import ParserRegistry

    for ct in (ContentType.HTML, ContentType.WEB_PAGE):
        _check(ParserRegistry().parse(PAGE.encode(), ct))


def test_orchestrator_html_chunk_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.ingestion.chunkers as chunkers
    from app.ingestion.content_classifier import ContentType
    from app.ingestion.orchestrator import IngestionOrchestrator

    # No registered chunker for the strategy: the HTML fallback runs.
    monkeypatch.setattr(chunkers, "get_chunker_for_strategy", lambda strategy: None)
    _check("\n".join(IngestionOrchestrator()._chunk(PAGE, ContentType.HTML, "semantic")))


def test_email_html_part() -> None:
    from app.ingestion.parsers.email_parser import EmailParser

    raw = ("From: ops@example.org\nTo: kb@example.org\nSubject: Reefer\n"
           "Content-Type: text/html; charset=utf-8\n\n" + PAGE)
    _check("\n".join(EmailParser().parse(raw)))


def test_confluence_ingestor() -> None:
    from app.knowledge.ingestors.confluence_ingestor import _html_to_text

    _check(_html_to_text(PAGE))


def test_web_augmented_page_text() -> None:
    from app.rag.agentic.patterns.web_augmented import _html_to_text

    _check(_html_to_text(PAGE))


async def _connector_text(connector: Any, config: Any, routes: dict[str, Any]) -> str:
    import httpx
    from unittest.mock import patch

    async def _send(self: Any, request: httpx.Request, **_: Any) -> httpx.Response:
        for prefix, payload in routes.items():
            if str(request.url).startswith(prefix):
                return httpx.Response(200, json=payload, request=request)
        return httpx.Response(200, json={"results": [], "articles": []}, request=request)

    with patch.object(httpx.AsyncClient, "send", _send):
        docs = [d async for d, _ in connector.get_delta(config, None)]
    return "\n".join(d.content.decode() for d in docs)


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    real = socket.getaddrinfo

    def fake(host: Any, *args: Any, **kwargs: Any) -> Any:
        if str(host).endswith(("example.org", "zendesk.com", "atlassian.net")):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.mark.asyncio
async def test_zendesk_articles(public_dns: None) -> None:
    from app.ingestion.connectors.zendesk_connector import ZendeskConnector
    from app.ingestion.source_config import SourceConfig

    config = SourceConfig(
        source_id="s", tenant_id="t", name="z", family="support", source_type="zendesk",
        connection_config={"subdomain": "acme", "email": "a@example.org", "api_token": "x",
                           "ingest_types": ["articles"]},
    )
    text = await _connector_text(ZendeskConnector(), config, {
        "https://acme.zendesk.com/api/v2/help_center/articles": {"articles": [
            {"id": 42, "title": "Reefer plug-in", "body": PAGE, "updated_at": "2026-10-01",
             "html_url": "https://acme.zendesk.com/hc/42"}]},
    })
    _check(text)


@pytest.mark.asyncio
async def test_confluence_connector(public_dns: None) -> None:
    from app.ingestion.connectors.confluence_connector import ConfluenceConnector
    from app.ingestion.source_config import SourceConfig

    config = SourceConfig(
        source_id="s", tenant_id="t", name="c", family="document_store",
        source_type="confluence",
        connection_config={"base_url": "https://acme.atlassian.net/wiki", "username": "a",
                           "api_token": "x", "space_keys": ["OPS"], "content_types": ["page"]},
    )
    text = await _connector_text(ConfluenceConnector(), config, {
        "https://acme.atlassian.net/wiki/rest/api/content": {"results": [
            {"id": "7", "title": "Reefer plug-in", "type": "page",
             "version": {"when": "2026-10-01T00:00:00Z"},
             "body": {"view": {"value": PAGE}}}]},
    })
    _check(text)


@pytest.mark.asyncio
async def test_rpa_page_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    import respx

    import app.net.ssrf_guard as g
    from app.rpa.executor import RPAExecutor

    monkeypatch.setattr(g, "_resolve_host", lambda host: ["93.184.216.34"])
    page = f"<html><head><title>KB 42</title></head><body>{PAGE}</body></html>"
    with respx.mock:
        respx.get("https://help.example.org/kb/42").mock(return_value=httpx.Response(
            200, content=page.encode("cp1252"),
            headers={"content-type": "text/html; charset=windows-1252"}))
        text, title = await RPAExecutor._http_fetch_text("https://help.example.org/kb/42")
    assert title == "KB 42"
    _check(text)
