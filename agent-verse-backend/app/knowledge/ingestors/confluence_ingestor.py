"""Confluence Cloud/Server page ingestor via REST API v1."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from app.ingestion.connector_egress import assert_source_url, source_client
from app.observability.logging import get_logger

logger = get_logger(__name__)
_CHUNK_SIZE = 1200


def _html_to_text(html: str) -> str:
    """Readable text of a page body (the upload HTML extractor, P1d-10)."""
    from app.ingestion.parsers.html_parser import html_to_text

    return html_to_text(html)


class ConfluenceIngestor:
    def __init__(self, base_url: str, token: str, user: str) -> None:
        self._base = base_url.rstrip("/")
        self._auth = (user, token)

    async def _fetch_pages(
        self, space_key: str, start: int = 0, limit: int = 50
    ) -> list[dict[str, Any]]:
        url = f"{self._base}/rest/api/content"
        # base_url is tenant-supplied (/ingest/confluence); whatever it returns
        # is indexed into the tenant's collection — egress-guard it (SSRF).
        await asyncio.to_thread(assert_source_url, url, context="confluence_ingest")
        params: dict[str, str | int] = {
            "spaceKey": space_key,
            "type": "page",
            "status": "current",
            "expand": "body.storage,version,space,ancestors",
            "limit": limit,
            "start": start,
        }
        async with source_client(timeout=30, auth=self._auth) as c:
            r = await c.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            return cast(list[dict[str, Any]], data.get("results", []))

    async def ingest_space(self, space_key: str, max_pages: int = 1000) -> list[dict[str, Any]]:
        from app.knowledge.ingestors.limits import MAX_CONFLUENCE_PAGES, clamp_limit

        max_pages = clamp_limit(max_pages, MAX_CONFLUENCE_PAGES)
        chunks: list[dict[str, Any]] = []
        start = 0
        pages_processed = 0

        while pages_processed < max_pages:
            pages = await self._fetch_pages(space_key, start=start)
            if not pages:
                break

            for page in pages:
                if pages_processed >= max_pages:
                    break
                html = page.get("body", {}).get("storage", {}).get("value", "")
                text = _html_to_text(html).strip()
                if len(text) < 50:
                    pages_processed += 1
                    continue

                page_id = page.get("id", "")
                page_url = f"{self._base}/pages/{page_id}"
                title = page.get("title", "")
                space_name = page.get("space", {}).get("name", space_key)

                # Chunk the page content
                start_pos = 0
                while start_pos < len(text):
                    chunk = text[start_pos : start_pos + _CHUNK_SIZE]
                    if len(chunk.strip()) >= 30:
                        chunks.append(
                            {
                                "content": f"# {title}\n\n{chunk}" if start_pos == 0 else chunk,
                                "source_url": page_url,
                                "source_type": "confluence",
                                "source_doc_id": page_id,
                                "page_number": None,
                                "metadata": {
                                    "title": title,
                                    "space": space_name,
                                    "space_key": space_key,
                                    "page_id": page_id,
                                },
                            }
                        )
                    start_pos += 1100
                pages_processed += 1

            start += len(pages)
            if len(pages) < 50:
                break

        logger.info(
            "confluence_space_ingested",
            space=space_key,
            pages=pages_processed,
            chunks=len(chunks),
        )
        return chunks
