"""SentryConnector — Sentry error tracking ingestion.

Cursor: last issue's lastSeen timestamp.
Yields: issues, events, and release notes.
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
_SENTRY_BASE = "https://sentry.io/api/0"


@register("sentry", feature_flag="ingestion_connector_sentry_enabled")
class SentryConnector(BaseConnector):
    """Sentry error tracking connector — issues and events."""

    source_type = "sentry"

    async def validate_connection(self, config: "SourceConfig") -> ConnectionHealth:
        import time, httpx
        t0 = time.perf_counter()
        try:
            token = config.connection_config.get("auth_token", "")
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{_SENTRY_BASE}/",
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(ok=True, latency_ms=latency, metadata={"api": "sentry.io"})
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: "SourceConfig", cursor: str | None
    ) -> AsyncIterator[tuple["RawDocument", str]]:
        from app.ingestion.source_config import RawDocument
        import httpx

        cc = config.connection_config
        token = cc.get("auth_token", "")
        org_slug = cc.get("org_slug", "")
        project_slugs = cc.get("project_slugs") or []
        batch_size = int(cc.get("batch_size", 100))
        base_url = cc.get("base_url", _SENTRY_BASE)
        headers = {"Authorization": f"Bearer {token}"}
        new_cursor = cursor or ""

        async with httpx.AsyncClient(timeout=30) as client:
            for project_slug in (project_slugs or [""]):
                url_path = f"{base_url}/projects/{org_slug}/{project_slug}/issues/" if project_slug else f"{base_url}/organizations/{org_slug}/issues/"
                params: dict = {"limit": batch_size, "sort": "date", "query": "is:unresolved"}
                if cursor:
                    params["query"] += f" lastSeen:>{cursor}"

                url: str | None = url_path
                while url:
                    r = await client.get(url, params=params, headers=headers)
                    if not r.is_success: break
                    issues = r.json()
                    if not isinstance(issues, list) or not issues: break

                    for issue in issues:
                        last_seen = issue.get("lastSeen", "")
                        new_cursor = max(new_cursor, last_seen)
                        culprit = issue.get("culprit", "")
                        level = issue.get("level", "")
                        count = issue.get("count", 0)
                        text = (
                            f"Sentry Issue: {issue.get('title', '')}\n"
                            f"ID: {issue.get('id')}  Level: {level}  Events: {count}\n"
                            f"Culprit: {culprit}  Last seen: {last_seen}\n\n"
                            f"Permalink: {issue.get('permalink', '')}"
                        )
                        doc = RawDocument(
                            doc_id=str(uuid.uuid4()),
                            source_id=config.source_id, tenant_id=config.tenant_id,
                            source_url=issue.get("permalink", ""),
                            content=text.encode(), content_type="text/plain",
                            metadata={"id": issue.get("id"), "level": level, "last_seen": last_seen, "project": project_slug},
                        )
                        yield doc, new_cursor

                    # Next page from Link header
                    link_header = r.headers.get("Link", "")
                    next_url = None
                    for part in link_header.split(","):
                        if 'rel="next"' in part and 'results="true"' in part:
                            next_url = part.strip().split(";")[0].strip().strip("<>")
                    url = next_url
                    params = {}
