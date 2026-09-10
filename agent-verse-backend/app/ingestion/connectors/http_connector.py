"""HttpApiConnector — generic HTTP/REST JSON API ingestion.

The universal fallback connector: ingest from *any* JSON HTTP endpoint without a
bespoke connector. Fetches a JSON response, extracts a list of records, and
yields one RawDocument per record with cursor-based incremental delta (LAW-03).

connection_config keys:
  url            (required)  the JSON endpoint (https, SSRF-guarded)
  method                     HTTP method (default "GET")
  headers                    dict of request headers (auth, etc.)
  params                     dict of static query params
  records_path               dot-path to the record list in the response
                             (e.g. "data.items"); "" tries common shapes
                             (a top-level list, or .results / .data / .items)
  id_field                   record field used as the document id (default "id")
  cursor_field               record field used as the incremental cursor
                             (e.g. "updated_at"); "" disables incremental
  cursor_param               query-param name to pass the cursor as on the
                             request (e.g. "updated_since"); optional
  content_fields             list of fields to join as the document text; ""
                             serializes the whole record as JSON
  title_field                record field used as the title (default "title")
  max_records                cap per sync (default 500)

Transport is a real HTTPS GET (SSRF-guarded, fail-closed). Non-2xx / parse
failures raise so the scheduler dead-letters the source and retries.
"""

from __future__ import annotations

import json as _json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register
from app.net.ssrf_guard import SSRFError, assert_public_url

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


def _dig(obj: Any, path: str) -> Any:
    """Follow a dot-path into nested dicts; return None if any hop is missing."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _extract_records(payload: Any, records_path: str) -> list[Any]:
    if records_path:
        found = _dig(payload, records_path)
        return list(found) if isinstance(found, list) else []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "data", "items", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


@register("http")
@register("rest")
class HttpApiConnector(BaseConnector):
    """Generic HTTP/REST JSON connector — ingest from any JSON endpoint."""

    source_type = "http"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        url = str(config.connection_config.get("url", "") or "")
        t0 = time.perf_counter()
        if not url:
            return ConnectionHealth(ok=False, error="connection_config.url is required")
        try:
            assert_public_url(url, context="http_connector.validate")
        except (SSRFError, ValueError) as exc:
            return ConnectionHealth(ok=False, error=f"url blocked: {exc}")
        try:
            payload = await self._fetch(config, cursor=None)
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))
        records = _extract_records(payload, str(config.connection_config.get("records_path", "")))
        return ConnectionHealth(
            ok=True,
            latency_ms=(time.perf_counter() - t0) * 1000,
            metadata={"records_sampled": len(records)},
        )

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        cc = config.connection_config
        url = str(cc.get("url", "") or "")
        if not url:
            _log.error("http_connector: no url in connection_config")
            return
        # SSRF egress guard — fail closed before any request.
        assert_public_url(url, context="http_connector.get_delta")

        id_field = str(cc.get("id_field", "id"))
        cursor_field = str(cc.get("cursor_field", ""))
        title_field = str(cc.get("title_field", "title"))
        content_fields = cc.get("content_fields") or []
        max_records = int(cc.get("max_records", 500))
        records_path = str(cc.get("records_path", ""))

        payload = await self._fetch(config, cursor=cursor)
        records = _extract_records(payload, records_path)

        new_cursor = cursor or ""
        emitted = 0
        for record in records:
            if emitted >= max_records:
                break
            if not isinstance(record, dict):
                continue

            record_cursor = str(record.get(cursor_field, "")) if cursor_field else ""
            # Incremental: skip records at/behind the stored cursor.
            if cursor_field and cursor and record_cursor and record_cursor <= cursor:
                continue
            if record_cursor:
                new_cursor = max(new_cursor, record_cursor)

            doc_id = str(record.get(id_field, "") or uuid.uuid4().hex)
            if content_fields:
                text = "\n".join(str(record.get(f, "")) for f in content_fields)
            else:
                text = _json.dumps(record, ensure_ascii=False, default=str)

            doc = RawDocument(
                doc_id=doc_id,
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=url,
                content=text.encode(),
                content_type="application/json" if not content_fields else "text/plain",
                title=str(record.get(title_field, "")),
                modified_at=record_cursor,
                metadata={"endpoint": url, "record_id": doc_id},
            )
            emitted += 1
            yield doc, new_cursor

    async def _fetch(self, config: SourceConfig, *, cursor: str | None) -> Any:
        """Perform the HTTP request and return the parsed JSON payload."""
        import httpx

        cc = config.connection_config
        url = str(cc.get("url", "") or "")
        method = str(cc.get("method", "GET")).upper()
        headers = dict(cc.get("headers", {}) or {})
        params = dict(cc.get("params", {}) or {})
        cursor_param = str(cc.get("cursor_param", ""))
        if cursor_param and cursor:
            params[cursor_param] = cursor
        timeout = float(cc.get("timeout_seconds", 15.0))

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, headers=headers, params=params)
            resp.raise_for_status()
            return resp.json()
