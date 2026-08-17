"""ConfluenceConnector — Atlassian Confluence page and space ingestion.

Wraps existing ConfluenceIngestor under the BaseConnector interface.
Cursor: last page modified timestamp (ISO 8601).
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, AsyncIterator

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


@register("confluence", feature_flag="ingestion_connector_confluence_enabled")
class ConfluenceConnector(BaseConnector):
    """Atlassian Confluence connector — pages, blogs, and spaces via REST API."""

    source_type = "confluence"
    supports_acl_propagation = True

    async def validate_connection(self, config: "SourceConfig") -> ConnectionHealth:
        import time
        t0 = time.perf_counter()
        try:
            import httpx
            cc = config.connection_config
            base_url = cc.get("base_url", "").rstrip("/")
            auth = (cc.get("username", ""), cc.get("api_token", ""))
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{base_url}/rest/api/space",
                    params={"limit": 1},
                    auth=auth,
                )
                r.raise_for_status()
                spaces = r.json().get("results", [])
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True, latency_ms=latency,
                metadata={"spaces_accessible": len(spaces)},
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: "SourceConfig", cursor: str | None
    ) -> AsyncIterator[tuple["RawDocument", str]]:
        from app.ingestion.source_config import RawDocument
        import httpx

        cc = config.connection_config
        base_url = cc.get("base_url", "").rstrip("/")
        auth = (cc.get("username", ""), cc.get("api_token", ""))
        space_keys = cc.get("space_keys") or []
        content_types = cc.get("content_types") or ["page", "blogpost"]
        batch_size = int(cc.get("batch_size", 50))

        new_cursor = cursor or ""

        async with httpx.AsyncClient(timeout=30) as client:
            for ctype in content_types:
                for space_key in (space_keys or [""]):
                    params: dict = {
                        "type": ctype,
                        "expand": "body.view,version,metadata.labels",
                        "limit": batch_size,
                    }
                    if space_key:
                        params["spaceKey"] = space_key
                    if cursor:
                        params["postingDay"] = cursor[:10]  # best effort date filter

                    url = f"{base_url}/rest/api/content"
                    while url:
                        r = await client.get(url, params=params, auth=auth)
                        if not r.is_success:
                            _log.warning("confluence: %s → %d", url, r.status_code)
                            break
                        data = r.json()
                        for page in data.get("results", []):
                            modified = page.get("version", {}).get("when", "")
                            if cursor and modified and modified <= cursor:
                                continue
                            new_cursor = max(new_cursor, modified)
                            body_html = page.get("body", {}).get("view", {}).get("value", "")
                            # Strip HTML tags
                            import re
                            text = re.sub(r"<[^>]+>", " ", body_html)
                            text = re.sub(r"\s+", " ", text).strip()
                            title = page.get("title", "")
                            full_text = f"# {title}\n\n{text}"
                            doc = RawDocument(
                                doc_id=str(uuid.uuid4()),
                                source_id=config.source_id,
                                tenant_id=config.tenant_id,
                                source_url=f"{base_url}/wiki/spaces/{space_key}/pages/{page.get('id')}",
                                content=full_text.encode(),
                                content_type="text/plain",
                                metadata={"title": title, "space": space_key, "type": ctype, "modified": modified},
                            )
                            yield doc, new_cursor
                        next_link = data.get("_links", {}).get("next")
                        url = f"{base_url}{next_link}" if next_link else None
                        params = {}  # next_link has all params embedded
