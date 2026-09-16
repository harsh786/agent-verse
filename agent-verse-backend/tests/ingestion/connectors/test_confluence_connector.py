"""Tests for ConfluenceConnector — validate_connection, get_delta pagination
via `_links.next`, HTML stripping, multi-space/content-type iteration, and
cursor-based skip logic. httpx is mocked throughout."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.ingestion.connectors.confluence_connector import ConfluenceConnector
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-c",
        tenant_id="t1",
        name="Test Confluence",
        family="document_store",
        source_type="confluence",
        enabled=True,
        connection_config=conn_config
        or {"base_url": "https://acme.atlassian.net/wiki", "username": "u", "api_token": "tok"},
    )


def _mock_async_client(get_impl):
    mock_client = AsyncMock()
    mock_client.get = get_impl
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


class TestValidateConnection:
    async def test_success(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"results": [{"key": "SPACE1"}, {"key": "SPACE2"}]})

        async def get(*a, **kw):
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            connector = ConfluenceConnector()
            health = await connector.validate_connection(_make_config())
        assert health.ok is True
        assert health.metadata["spaces_accessible"] == 2

    async def test_failure(self):
        async def get(*a, **kw):
            raise ConnectionError("dns failure")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            connector = ConfluenceConnector()
            health = await connector.validate_connection(_make_config())
        assert health.ok is False
        assert "dns failure" in health.error


def _page(page_id, title, body_html, modified):
    return {
        "id": page_id,
        "title": title,
        "version": {"when": modified},
        "body": {"view": {"value": body_html}},
    }


class TestGetDelta:
    async def test_single_page_result(self):
        pages = {"results": [_page("1", "Home", "<p>Hello <b>World</b></p>", "2026-01-01T00:00:00Z")]}

        async def get(url, params=None, auth=None):
            resp = MagicMock()
            resp.is_success = True
            resp.json = MagicMock(return_value=pages)
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            connector = ConfluenceConnector()
            config = _make_config(
                {
                    "base_url": "https://x.atlassian.net/wiki",
                    "space_keys": ["ENG"],
                    "content_types": ["page"],
                }
            )
            results = [d async for d in connector.get_delta(config, None)]

        assert len(results) == 1
        doc, cursor = results[0]
        assert "Hello World" in doc.content.decode()
        assert doc.metadata["title"] == "Home"
        assert cursor == "2026-01-01T00:00:00Z"

    async def test_pagination_via_links_next(self):
        base = "https://x.atlassian.net/wiki"
        page1 = {
            "results": [_page("1", "P1", "<p>one</p>", "2026-01-01T00:00:00Z")],
            "_links": {"next": "/rest/api/content?start=1"},
        }
        page2 = {
            "results": [_page("2", "P2", "<p>two</p>", "2026-01-02T00:00:00Z")],
            "_links": {},
        }
        call_count = 0

        async def get(url, params=None, auth=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.is_success = True
            resp.json = MagicMock(return_value=page1 if call_count == 1 else page2)
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            connector = ConfluenceConnector()
            config = _make_config({"base_url": base, "content_types": ["page"]})
            results = [d async for d in connector.get_delta(config, None)]

        assert call_count == 2
        assert len(results) == 2

    async def test_cursor_skips_already_seen_pages(self):
        pages = {
            "results": [_page("1", "Old", "<p>old</p>", "2026-01-01T00:00:00Z")],
        }

        async def get(*a, **kw):
            resp = MagicMock()
            resp.is_success = True
            resp.json = MagicMock(return_value=pages)
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            connector = ConfluenceConnector()
            config = _make_config()
            results = [d async for d in connector.get_delta(config, "2026-01-01T00:00:00Z")]
        assert results == []

    async def test_multi_space_and_content_type_iteration(self):
        async def get(url, params=None, auth=None):
            resp = MagicMock()
            resp.is_success = True
            space = params.get("spaceKey", "none") if params else "none"
            ctype = params.get("type", "none") if params else "none"
            resp.json = MagicMock(
                return_value={
                    "results": [_page(f"{space}-{ctype}", f"{space}/{ctype}", "<p>x</p>", "2026-01-01T00:00:00Z")]
                }
            )
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            connector = ConfluenceConnector()
            config = _make_config(
                {
                    "base_url": "https://x.atlassian.net/wiki",
                    "space_keys": ["ENG", "SALES"],
                    "content_types": ["page", "blogpost"],
                }
            )
            results = [d async for d in connector.get_delta(config, None)]
        # 2 spaces * 2 content types = 4 docs
        assert len(results) == 4
        combos = {(d.metadata["space"], d.metadata["type"]) for d, _ in results}
        assert combos == {("ENG", "page"), ("ENG", "blogpost"), ("SALES", "page"), ("SALES", "blogpost")}

    async def test_failed_response_breaks_loop(self):
        async def get(*a, **kw):
            resp = MagicMock()
            resp.is_success = False
            resp.status_code = 403
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            connector = ConfluenceConnector()
            results = [d async for d in connector.get_delta(_make_config(), None)]
        assert results == []


def test_source_type_and_registration():
    from app.ingestion.connector_registry import get_connector

    assert ConfluenceConnector().source_type == "confluence"
    assert get_connector("confluence") is ConfluenceConnector
    assert ConfluenceConnector.supports_acl_propagation is True
