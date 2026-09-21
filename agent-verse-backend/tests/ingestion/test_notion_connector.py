"""NotionConnector — real functional scenarios against a mocked Notion REST API.

httpx.AsyncClient is patched (no real network calls). Low-level API wrapper
methods (_get/_post/list_pages/fetch_page_content/...) are exercised against
realistic Notion API response shapes, including pagination, 4xx/5xx errors,
and timeouts. The BaseConnector adapter (NotionSourceConnector) is exercised
by mocking the wrapper's public async methods directly, matching this repo's
existing convention (see test_document_store_connectors.py).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.ingestion.connector_registry import get_connector, load_all_connectors
from app.ingestion.connectors.notion_connector import (
    NotionConnector,
    NotionSourceConnector,
)
from app.ingestion.source_config import SourceConfig, SourceFamily


def _config(**cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-notion",
        tenant_id="t1",
        name="notion-src",
        family=SourceFamily.DOCUMENT_STORE,
        source_type="notion",
        connection_config=cc,
    )


def _patch_httpx(responses: list[Any], statuses: list[int] | None = None) -> Any:
    """Patch httpx.AsyncClient so each .get/.post call pops the next response.

    ``responses`` is a list of JSON payloads (or exceptions to raise). ``statuses``
    optionally gives an HTTP status per call — anything >= 400 makes
    raise_for_status() raise httpx.HTTPStatusError.
    """
    queue = list(responses)
    status_queue = list(statuses) if statuses else [200] * len(responses)

    def _factory(*args: Any, **kwargs: Any) -> Any:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=ctx)
        ctx.__aexit__ = AsyncMock(return_value=False)

        async def _call(url: str, **_kw: Any) -> Any:
            payload = queue.pop(0)
            status = status_queue.pop(0)
            if isinstance(payload, Exception):
                raise payload
            resp = MagicMock()
            resp.status_code = status
            resp.json = MagicMock(return_value=payload)
            if status >= 400:
                request = httpx.Request("GET", url)
                response = httpx.Response(status, request=request)
                resp.raise_for_status = MagicMock(
                    side_effect=httpx.HTTPStatusError(
                        f"HTTP {status}", request=request, response=response
                    )
                )
            else:
                resp.raise_for_status = MagicMock()
            return resp

        ctx.get = AsyncMock(side_effect=_call)
        ctx.post = AsyncMock(side_effect=_call)
        return ctx

    return patch("httpx.AsyncClient", _factory)


class TestNotionConnectorLowLevel:
    async def test_get_sends_auth_headers(self) -> None:
        connector = NotionConnector(api_key="secret_abc")
        with _patch_httpx([{"id": "p1"}]) as _p:
            data = await connector._get("pages/p1")
        assert data == {"id": "p1"}
        assert connector._headers["Authorization"] == "Bearer secret_abc"
        assert connector._headers["Notion-Version"] == "2022-06-28"

    async def test_post_sends_json_body(self) -> None:
        connector = NotionConnector(api_key="secret_abc")
        with _patch_httpx([{"results": []}]):
            data = await connector._post("databases/db1/query", {"page_size": 10})
        assert data == {"results": []}

    async def test_list_pages_single_page(self) -> None:
        connector = NotionConnector(api_key="k")
        payload = {"results": [{"id": "p1"}, {"id": "p2"}], "has_more": False}
        with _patch_httpx([payload]):
            pages = await connector.list_pages("db1")
        assert [p["id"] for p in pages] == ["p1", "p2"]

    async def test_list_pages_paginates_until_has_more_false(self) -> None:
        connector = NotionConnector(api_key="k")
        page1 = {"results": [{"id": "p1"}], "has_more": True, "next_cursor": "c2"}
        page2 = {"results": [{"id": "p2"}], "has_more": False}
        with _patch_httpx([page1, page2]):
            pages = await connector.list_pages("db1", page_size=1)
        assert [p["id"] for p in pages] == ["p1", "p2"]

    async def test_fetch_page_content_converts_blocks_to_text(self) -> None:
        connector = NotionConnector(api_key="k")
        payload = {
            "results": [
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Body text"}]}}
            ]
        }
        with _patch_httpx([payload]):
            text = await connector.fetch_page_content("page1")
        assert text == "Body text"

    async def test_fetch_page_metadata_returns_raw_page_object(self) -> None:
        connector = NotionConnector(api_key="k")
        payload = {"id": "page1", "url": "https://notion.so/page1", "created_time": "t"}
        with _patch_httpx([payload]):
            meta = await connector.fetch_page_metadata("page1")
        assert meta == payload

    async def test_list_all_pages_in_workspace_paginates(self) -> None:
        connector = NotionConnector(api_key="k")
        page1 = {"results": [{"id": "a"}], "has_more": True, "next_cursor": "c2"}
        page2 = {"results": [{"id": "b"}], "has_more": False}
        with _patch_httpx([page1, page2]):
            pages = await connector.list_all_pages_in_workspace(page_size=1)
        assert [p["id"] for p in pages] == ["a", "b"]

    async def test_get_raises_on_4xx(self) -> None:
        connector = NotionConnector(api_key="bad-key")
        with _patch_httpx([{"message": "unauthorized"}], statuses=[401]):
            with pytest.raises(httpx.HTTPStatusError):
                await connector.fetch_page_metadata("page1")

    async def test_post_raises_on_5xx(self) -> None:
        connector = NotionConnector(api_key="k")
        with _patch_httpx([{"message": "server error"}], statuses=[500]):
            with pytest.raises(httpx.HTTPStatusError):
                await connector.list_pages("db1")

    async def test_timeout_propagates(self) -> None:
        connector = NotionConnector(api_key="k")
        with _patch_httpx([httpx.TimeoutException("timed out")]):
            with pytest.raises(httpx.TimeoutException):
                await connector.fetch_page_metadata("page1")

    def test_blocks_to_text_malformed_block_missing_type_field(self) -> None:
        connector = NotionConnector(api_key="k")
        # A block with no "type" key at all must not raise — falls back to "".
        result = connector._blocks_to_text([{"unexpected": "shape"}])
        assert result == ""


class TestNotionSourceConnectorRegistration:
    def test_registered(self) -> None:
        load_all_connectors()
        assert get_connector("notion") is NotionSourceConnector

    def test_client_reads_api_key_from_config(self) -> None:
        conn = NotionSourceConnector()
        cfg = _config(api_key="secret_xyz")
        client = conn._client(cfg)
        assert client._api_key == "secret_xyz"


class TestNotionSourceConnectorValidateConnection:
    async def test_success_with_database_id(self) -> None:
        conn = NotionSourceConnector()
        cfg = _config(api_key="k", database_id="db1")
        with patch.object(
            NotionConnector, "list_pages", new=AsyncMock(return_value=[{"id": "p1"}])
        ) as mock_list:
            health = await conn.validate_connection(cfg)
        assert health.ok is True
        assert health.metadata["pages_visible"] == 1
        mock_list.assert_awaited_once_with("db1", page_size=1)

    async def test_success_without_database_id_searches_workspace(self) -> None:
        conn = NotionSourceConnector()
        cfg = _config(api_key="k")
        with patch.object(
            NotionConnector,
            "list_all_pages_in_workspace",
            new=AsyncMock(return_value=[{"id": "p1"}, {"id": "p2"}]),
        ) as mock_search:
            health = await conn.validate_connection(cfg)
        assert health.ok is True
        assert health.metadata["pages_visible"] == 2
        mock_search.assert_awaited_once_with(page_size=1)

    async def test_failure_returns_unhealthy(self) -> None:
        conn = NotionSourceConnector()
        cfg = _config(api_key="bad")
        with patch.object(
            NotionConnector,
            "list_all_pages_in_workspace",
            new=AsyncMock(side_effect=RuntimeError("401 unauthorized")),
        ):
            health = await conn.validate_connection(cfg)
        assert health.ok is False
        assert "401" in health.error


class TestNotionSourceConnectorGetDelta:
    async def test_with_database_id_uses_list_pages(self) -> None:
        conn = NotionSourceConnector()
        cfg = _config(api_key="k", database_id="db1")
        pages = [
            {
                "id": "p1",
                "url": "https://notion.so/p1",
                "last_edited_time": "2026-01-02T00:00:00Z",
                "properties": {
                    "Name": {"type": "title", "title": [{"plain_text": "My Page"}]}
                },
            }
        ]
        with (
            patch.object(NotionConnector, "list_pages", new=AsyncMock(return_value=pages)),
            patch.object(
                NotionConnector,
                "fetch_page_content",
                new=AsyncMock(return_value="page body text"),
            ),
        ):
            docs = [d async for d in conn.get_delta(cfg, None)]
        assert len(docs) == 1
        raw, cursor = docs[0]
        assert raw.content == b"page body text"
        assert raw.title == "My Page"
        assert raw.source_url == "https://notion.so/p1"
        assert raw.metadata == {"notion_page_id": "p1", "source_type": "notion"}
        assert cursor == "2026-01-02T00:00:00Z"

    async def test_without_database_id_uses_workspace_search(self) -> None:
        conn = NotionSourceConnector()
        cfg = _config(api_key="k")
        pages = [{"id": "p1", "url": "u", "last_edited_time": "t1", "properties": {}}]
        with (
            patch.object(
                NotionConnector,
                "list_all_pages_in_workspace",
                new=AsyncMock(return_value=pages),
            ) as mock_search,
            patch.object(
                NotionConnector, "fetch_page_content", new=AsyncMock(return_value="body")
            ),
        ):
            docs = [d async for d in conn.get_delta(cfg, None)]
        assert len(docs) == 1
        mock_search.assert_awaited_once()

    async def test_skips_pages_without_id(self) -> None:
        conn = NotionSourceConnector()
        cfg = _config(api_key="k", database_id="db1")
        pages = [{"url": "u", "last_edited_time": "t1", "properties": {}}]
        with (
            patch.object(NotionConnector, "list_pages", new=AsyncMock(return_value=pages)),
            patch.object(
                NotionConnector, "fetch_page_content", new=AsyncMock(return_value="body")
            ),
        ):
            docs = [d async for d in conn.get_delta(cfg, None)]
        assert docs == []

    async def test_skips_empty_page_content(self) -> None:
        conn = NotionSourceConnector()
        cfg = _config(api_key="k", database_id="db1")
        pages = [{"id": "p1", "url": "u", "last_edited_time": "t1", "properties": {}}]
        with (
            patch.object(NotionConnector, "list_pages", new=AsyncMock(return_value=pages)),
            patch.object(
                NotionConnector, "fetch_page_content", new=AsyncMock(return_value="   ")
            ),
        ):
            docs = [d async for d in conn.get_delta(cfg, None)]
        assert docs == []

    async def test_skips_pages_not_newer_than_cursor(self) -> None:
        conn = NotionSourceConnector()
        cfg = _config(api_key="k", database_id="db1")
        pages = [
            {"id": "old", "url": "u1", "last_edited_time": "2026-01-01T00:00:00Z",
             "properties": {}},
            {"id": "new", "url": "u2", "last_edited_time": "2026-01-05T00:00:00Z",
             "properties": {}},
        ]
        with (
            patch.object(NotionConnector, "list_pages", new=AsyncMock(return_value=pages)),
            patch.object(
                NotionConnector, "fetch_page_content", new=AsyncMock(return_value="body")
            ),
        ):
            docs = [
                d async for d in conn.get_delta(cfg, "2026-01-02T00:00:00Z")
            ]
        assert len(docs) == 1
        raw, cursor = docs[0]
        assert raw.metadata["notion_page_id"] == "new"
        assert cursor == "2026-01-05T00:00:00Z"

    async def test_title_falls_back_to_empty_when_no_title_property(self) -> None:
        conn = NotionSourceConnector()
        cfg = _config(api_key="k", database_id="db1")
        pages = [
            {
                "id": "p1",
                "url": "u",
                "last_edited_time": "t1",
                "properties": {"Status": {"type": "select", "select": {"name": "Done"}}},
            }
        ]
        with (
            patch.object(NotionConnector, "list_pages", new=AsyncMock(return_value=pages)),
            patch.object(
                NotionConnector, "fetch_page_content", new=AsyncMock(return_value="body")
            ),
        ):
            docs = [d async for d in conn.get_delta(cfg, None)]
        assert docs[0][0].title == ""


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
