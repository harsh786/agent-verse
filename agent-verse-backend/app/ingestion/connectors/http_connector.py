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

import asyncio
import json as _json
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorFetchError,
    row_identity,
    stable_doc_id,
)
from app.ingestion.connector_egress import (
    GuardedFetch,
    assert_source_url,
    guarded_fetch,
    source_client,
)
from app.ingestion.connector_registry import register
from app.ingestion.source_config import CONNECTOR_MOVED_KEY
from app.net.ssrf_guard import SSRFError

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


def _extract_records(payload: Any, records_path: str, *, strict: bool = False) -> list[Any]:
    """The record list of ``payload``.

    ``strict`` (a sync): a response with no record list where one was expected
    raises :class:`ConnectorFetchError` — an error body or a wrong
    ``records_path`` used to read as "0 records", a successful empty sync.
    """
    if records_path:
        found = _dig(payload, records_path)
        if isinstance(found, list):
            return list(found)
        if strict:
            raise ConnectorFetchError(
                f"http: the response has no record list at records_path {records_path!r}"
            )
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "data", "items", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    if strict:
        raise ConnectorFetchError(
            "http: the response holds no record list (a top-level list, or "
            "results / data / items / records); set records_path"
        )
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
            # The same operator egress policy as every connector (EGRESS-NET).
            await asyncio.to_thread(assert_source_url, url, context="http_connector.validate")
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
            # USR-1: nothing can be fetched — fail, never an empty success.
            raise ConnectorFetchError("http: connection_config.url is required")
        # SSRF egress guard — fail closed before any request.
        await asyncio.to_thread(assert_source_url, url, context="http_connector.get_delta")

        id_field = str(cc.get("id_field", "id"))
        cursor_field = str(cc.get("cursor_field", ""))
        title_field = str(cc.get("title_field", "title"))
        content_fields = cc.get("content_fields") or []
        max_records = int(cc.get("max_records", 500))
        records_path = str(cc.get("records_path", ""))

        payload, fetched = await self._fetch_with_path(config, cursor=cursor)
        records = _extract_records(payload, records_path, strict=True)
        fetch_meta: dict[str, Any] = {"final_url": fetched.final_url}
        if (moved := fetched.move_notice()) is not None:
            fetch_meta[CONNECTOR_MOVED_KEY] = moved

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

            record_id = record.get(id_field)
            doc_id = stable_doc_id(
                config, url, record_id if record_id not in (None, "") else row_identity(record)
            )
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
                metadata={"endpoint": url, "record_id": doc_id, **fetch_meta},
            )
            emitted += 1
            yield doc, new_cursor

    async def _fetch(self, config: SourceConfig, *, cursor: str | None) -> Any:
        """The parsed JSON payload of the endpoint."""
        payload, _fetched = await self._fetch_with_path(config, cursor=cursor)
        return payload

    async def _fetch_with_path(
        self, config: SourceConfig, *, cursor: str | None
    ) -> tuple[Any, GuardedFetch]:
        """Perform the HTTP request; the parsed JSON payload and the redirect path.

        The URL was SSRF-checked by the caller; the pinned client re-checks at
        connect time (a plain client re-resolved the name — DNS rebinding).
        """
        cc = config.connection_config
        url = str(cc.get("url", "") or "")
        method = str(cc.get("method", "GET")).upper()
        headers = dict(cc.get("headers", {}) or {})
        params = dict(cc.get("params", {}) or {})
        cursor_param = str(cc.get("cursor_param", ""))
        if cursor_param and cursor:
            params[cursor_param] = cursor
        timeout = float(cc.get("timeout_seconds", 15.0))

        async with source_client(timeout=timeout) as client:
            # USR-5: a moved endpoint is followed (every hop egress-checked, at
            # most 5); the final URL and a permanent move are recorded.
            fetched = await guarded_fetch(
                client, method, url, context="http_connector",
                headers=headers, params=params,
            )
            fetched.response.raise_for_status()
            return fetched.response.json(), fetched
