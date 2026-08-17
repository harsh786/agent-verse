"""ZendeskConnector — Zendesk tickets, articles, and comments.

Cursor: last ticket/article updated_at timestamp.
Supports incremental export via Zendesk's Incremental Exports API.
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


@register("zendesk", feature_flag="ingestion_connector_zendesk_enabled")
class ZendeskConnector(BaseConnector):
    """Zendesk connector — tickets, articles, and comments via REST API."""

    source_type = "zendesk"

    async def validate_connection(self, config: "SourceConfig") -> ConnectionHealth:
        import time, httpx
        t0 = time.perf_counter()
        try:
            cc = config.connection_config
            subdomain = cc.get("subdomain", "")
            token = cc.get("api_token", "")
            email = cc.get("email", "")
            base = f"https://{subdomain}.zendesk.com/api/v2"
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{base}/users/me.json",
                    auth=(f"{email}/token", token),
                )
                r.raise_for_status()
                user = r.json().get("user", {})
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(ok=True, latency_ms=latency, metadata={"user": user.get("name"), "subdomain": subdomain})
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: "SourceConfig", cursor: str | None
    ) -> AsyncIterator[tuple["RawDocument", str]]:
        from app.ingestion.source_config import RawDocument
        import httpx

        cc = config.connection_config
        subdomain = cc.get("subdomain", "")
        token = cc.get("api_token", "")
        email = cc.get("email", "")
        base = f"https://{subdomain}.zendesk.com/api/v2"
        auth = (f"{email}/token", token)
        ingest_types = cc.get("ingest_types") or ["tickets"]
        new_cursor = cursor or ""

        async with httpx.AsyncClient(timeout=30) as client:
            if "tickets" in ingest_types:
                # Zendesk Incremental Ticket Export
                import time as _time
                start_time = int(cursor) if cursor and cursor.isdigit() else int(_time.time()) - 86400 * 30
                url: str | None = f"{base}/incremental/tickets.json"
                params: dict = {"start_time": start_time}
                while url:
                    r = await client.get(url, params=params, auth=auth)
                    if not r.is_success: break
                    data = r.json()
                    for ticket in data.get("tickets", []):
                        if ticket.get("status") == "deleted":
                            continue
                        updated = ticket.get("updated_at", "")
                        new_cursor = str(data.get("end_time", new_cursor))
                        text = (
                            f"Ticket #{ticket.get('id')}: {ticket.get('subject', '')}\n"
                            f"Status: {ticket.get('status')}  Priority: {ticket.get('priority', 'normal')}\n"
                            f"Updated: {updated}\n\n{ticket.get('description') or ''}"
                        )
                        doc = RawDocument(
                            doc_id=str(uuid.uuid4()),
                            source_id=config.source_id, tenant_id=config.tenant_id,
                            source_url=f"https://{subdomain}.zendesk.com/agent/tickets/{ticket.get('id')}",
                            content=text.encode(), content_type="text/plain",
                            metadata={"id": ticket.get("id"), "status": ticket.get("status"), "updated": updated},
                        )
                        yield doc, new_cursor
                    if data.get("end_of_stream"):
                        break
                    url = data.get("next_page")
                    params = {}

            if "articles" in ingest_types:
                # Help center articles
                url = f"{base}/help_center/articles.json"
                params = {"sort_by": "updated_at", "sort_order": "asc", "per_page": 100}
                while url:
                    r = await client.get(url, params=params, auth=auth)
                    if not r.is_success: break
                    data = r.json()
                    for article in data.get("articles", []):
                        updated = article.get("updated_at", "")
                        if cursor and updated <= cursor:
                            continue
                        new_cursor = max(new_cursor, updated)
                        import re
                        text = re.sub(r"<[^>]+>", " ", article.get("body", ""))
                        full_text = f"# {article.get('title', '')}\n\n{text}"
                        doc = RawDocument(
                            doc_id=str(uuid.uuid4()),
                            source_id=config.source_id, tenant_id=config.tenant_id,
                            source_url=article.get("html_url", ""),
                            content=full_text.encode(), content_type="text/plain",
                            metadata={"id": article.get("id"), "title": article.get("title"), "type": "article"},
                        )
                        yield doc, new_cursor
                    url = data.get("next_page")
                    params = {}
