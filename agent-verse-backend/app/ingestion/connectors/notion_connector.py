"""NotionConnector — fetches pages from the Notion API for ingestion.

Usage
-----
connector = NotionConnector(api_key="secret_...")
pages = await connector.list_pages(database_id="abc123")
content = await connector.fetch_page_content(page_id="xyz...")
"""

from __future__ import annotations

from typing import Any


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
