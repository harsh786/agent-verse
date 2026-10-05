"""ZendeskConnector — Zendesk tickets, articles, and comments.

Cursor: last ticket/article updated_at timestamp.
Supports incremental export via Zendesk's Incremental Exports API.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    UnitFailures,
    stable_doc_id,
)
from app.ingestion.connector_egress import ConnectorEgressBlockedError
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


_SUBDOMAIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def _zendesk_base(subdomain: object) -> str:
    """``https://<subdomain>.zendesk.com/api/v2`` for a *label-only* subdomain.

    The subdomain used to be interpolated raw, so ``"127.0.0.1:1/#"`` produced
    ``https://127.0.0.1:1/#.zendesk.com/...`` — the tenant chose the host. A
    single DNS label pins every request to Zendesk's own domain.
    """
    value = str(subdomain or "")
    if not _SUBDOMAIN_RE.match(value):
        raise ConnectorEgressBlockedError(
            f"SSRF guard [zendesk]: subdomain {value[:60]!r} is not a DNS label — blocked"
        )
    return f"https://{value}.zendesk.com/api/v2"


def _check_next_page(url: str, base: str) -> str:
    """``next_page`` comes from the response body — it must stay on the tenant's host."""
    if urlsplit(url).hostname != urlsplit(base).hostname or urlsplit(url).scheme != "https":
        raise ConnectorEgressBlockedError(
            "SSRF guard [zendesk]: next_page left the Zendesk host — blocked"
        )
    return url


@register("zendesk", feature_flag="ingestion_connector_zendesk_enabled")
class ZendeskConnector(BaseConnector):
    """Zendesk connector — tickets, articles, and comments via REST API."""

    source_type = "zendesk"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        import httpx

        t0 = time.perf_counter()
        try:
            cc = config.connection_config
            subdomain = cc.get("subdomain", "")
            token = cc.get("api_token", "")
            email = cc.get("email", "")
            base = _zendesk_base(subdomain)
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{base}/users/me.json",
                    auth=(f"{email}/token", token),
                )
                r.raise_for_status()
                user = r.json().get("user", {})
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True,
                latency_ms=latency,
                metadata={"user": user.get("name"), "subdomain": subdomain},
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        import httpx

        from app.ingestion.source_config import RawDocument

        cc = config.connection_config
        subdomain = cc.get("subdomain", "")
        token = cc.get("api_token", "")
        email = cc.get("email", "")
        base = _zendesk_base(subdomain)
        auth = (f"{email}/token", token)
        ingest_types = cc.get("ingest_types") or ["tickets"]
        new_cursor = cursor or ""

        failures = UnitFailures("zendesk")
        async with httpx.AsyncClient(timeout=30) as client:
            if "tickets" in ingest_types:
                # Zendesk Incremental Ticket Export
                import time as _time

                start_time = (
                    int(cursor) if cursor and cursor.isdigit() else int(_time.time()) - 86400 * 30
                )
                url: str | None = f"{base}/incremental/tickets.json"
                params: dict = {"start_time": start_time}
                while url:
                    r = await client.get(url, params=params, auth=auth)
                    if not r.is_success:
                        failures.add("tickets", r)
                        break
                    data = r.json()
                    for ticket in data.get("tickets", []):
                        if ticket.get("status") == "deleted":
                            continue
                        updated = ticket.get("updated_at", "")
                        new_cursor = str(data.get("end_time", new_cursor))
                        text = (
                            f"Ticket #{ticket.get('id')}: {ticket.get('subject', '')}\n"
                            f"Status: {ticket.get('status')}  Priority: {ticket.get('priority', 'normal')}\n"  # noqa: E501
                            f"Updated: {updated}\n\n{ticket.get('description') or ''}"
                        )
                        doc = RawDocument(
                            doc_id=stable_doc_id(config, "ticket", ticket.get("id")),
                            source_id=config.source_id,
                            tenant_id=config.tenant_id,
                            source_url=f"https://{subdomain}.zendesk.com/agent/tickets/{ticket.get('id')}",
                            content=text.encode(),
                            content_type="text/plain",
                            metadata={
                                "id": ticket.get("id"),
                                "status": ticket.get("status"),
                                "updated": updated,
                            },
                        )
                        yield doc, new_cursor
                    if data.get("end_of_stream"):
                        break
                    next_page = data.get("next_page")
                    url = _check_next_page(next_page, base) if next_page else None
                    params = {}

            if "articles" in ingest_types:
                # Help center articles
                url = f"{base}/help_center/articles.json"
                params = {"sort_by": "updated_at", "sort_order": "asc", "per_page": 100}
                while url:
                    r = await client.get(url, params=params, auth=auth)
                    if not r.is_success:
                        failures.add("help center articles", r)
                        break
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
                            doc_id=stable_doc_id(config, "article", article.get("id")),
                            source_id=config.source_id,
                            tenant_id=config.tenant_id,
                            source_url=article.get("html_url", ""),
                            content=full_text.encode(),
                            content_type="text/plain",
                            metadata={
                                "id": article.get("id"),
                                "title": article.get("title"),
                                "type": "article",
                            },
                        )
                        yield doc, new_cursor
                    next_page = data.get("next_page")
                    url = _check_next_page(next_page, base) if next_page else None
                    params = {}
        # USR-1: an unreadable ticket export / article listing fails the sync
        # (partial) — it used to end it as an empty success.
        failures.raise_if_any()
