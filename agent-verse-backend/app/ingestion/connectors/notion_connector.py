"""NotionConnector — fetches pages from the Notion API for ingestion.

Usage
-----
connector = NotionConnector(api_key="secret_...")
pages = await connector.list_pages(database_id="abc123")
content = await connector.fetch_page_content(page_id="xyz...")
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig


class NotionConnector:
    """Thin async wrapper around the Notion REST API v1."""

    _API_BASE = "https://api.notion.com/v1"
    _NOTION_VERSION = "2022-06-28"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": self._NOTION_VERSION,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        import httpx

        url = f"{self._API_BASE}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers, params=params or {})
            resp.raise_for_status()
            return resp.json()

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        import httpx

        url = f"{self._API_BASE}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers, json=body)
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_pages(
        self,
        database_id: str,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        """Query a Notion database and return all page objects."""
        pages: list[dict[str, Any]] = []
        has_more = True
        start_cursor: str | None = None
        while has_more:
            payload: dict[str, Any] = {"page_size": page_size}
            if start_cursor:
                payload["start_cursor"] = start_cursor
            data = await self._post(f"databases/{database_id}/query", payload)
            pages.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
        return pages

    async def fetch_page_content(self, page_id: str) -> str:
        """Return the plain-text content of a Notion page by fetching its blocks."""
        data = await self._get(f"blocks/{page_id}/children", {"page_size": 100})
        blocks = data.get("results", [])
        return self._blocks_to_text(blocks)

    def _blocks_to_text(self, blocks: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for block in blocks:
            bt = block.get("type", "")
            rich = block.get(bt, {}).get("rich_text", [])
            text = "".join(r.get("plain_text", "") for r in rich)
            if text.strip():
                lines.append(text.strip())
        return "\n\n".join(lines)

    async def fetch_page_metadata(self, page_id: str) -> dict[str, Any]:
        """Return the page object (properties, created_time, url, etc.)."""
        return await self._get(f"pages/{page_id}")

    async def list_all_pages_in_workspace(self, page_size: int = 50) -> list[dict[str, Any]]:
        """Search for all pages accessible to the integration token."""
        pages: list[dict[str, Any]] = []
        has_more = True
        start_cursor: str | None = None
        while has_more:
            payload: dict[str, Any] = {
                "filter": {"property": "object", "value": "page"},
                "page_size": page_size,
            }
            if start_cursor:
                payload["start_cursor"] = start_cursor
            data = await self._post("search", payload)
            pages.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
        return pages


@register("notion", feature_flag="ingestion_connector_notion_enabled")
class NotionSourceConnector(BaseConnector):
    """BaseConnector adapter that routes Notion pages through the real pipeline.

    Reads credentials from ``config.connection_config`` (``api_key`` plus an
    optional ``database_id``) and yields one ``RawDocument`` per page. Cursor is
    the max ``last_edited_time`` seen, so re-syncs only pull newer pages (LAW-03).
    """

    source_type = "notion"

    def _client(self, config: SourceConfig) -> NotionConnector:
        return NotionConnector(api_key=config.connection_config.get("api_key", ""))

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        t0 = time.perf_counter()
        try:
            client = self._client(config)
            database_id = config.connection_config.get("database_id", "")
            if database_id:
                pages = await client.list_pages(database_id, page_size=1)
            else:
                pages = await client.list_all_pages_in_workspace(page_size=1)
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(ok=True, latency_ms=latency,
                                    metadata={"pages_visible": len(pages)})
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        client = self._client(config)
        database_id = config.connection_config.get("database_id", "")
        pages = (
            await client.list_pages(database_id)
            if database_id
            else await client.list_all_pages_in_workspace()
        )
        new_cursor = cursor or ""
        for page in pages:
            page_id = page.get("id", "")
            if not page_id:
                continue
            modified = page.get("last_edited_time", "")
            if cursor and modified and modified <= cursor:
                continue
            if modified:
                new_cursor = max(new_cursor, modified)
            text = await client.fetch_page_content(page_id)
            if not text.strip():
                continue
            title = ""
            props = page.get("properties", {})
            for prop in props.values():
                if prop.get("type") == "title":
                    title = "".join(
                        t.get("plain_text", "") for t in prop.get("title", [])
                    )
                    break
            doc = RawDocument(
                doc_id=str(uuid.uuid4()),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=page.get("url", ""),
                title=title,
                content=text.encode(),
                content_type="text/plain",
                modified_at=modified,
                metadata={"notion_page_id": page_id, "source_type": "notion"},
            )
            yield doc, new_cursor
