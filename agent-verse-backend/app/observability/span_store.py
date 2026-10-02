"""Tenant-scoped recent-span store (Redis-backed, bounded, TTL'd).

Backs ``GET /analytics/observability/spans`` — the in-app span waterfall. Spans
are exported through a ``BatchSpanProcessor`` (background thread, so Redis
writes never block the event loop) into one capped Redis list per tenant:

* only spans stamped with ``agentverse.tenant_id`` are stored — platform spans
  with no tenant are never shown to any tenant;
* each tenant list is trimmed to ``max_spans_per_tenant`` and expires after
  ``ttl_seconds`` of inactivity, so memory is bounded however many tenants run;
* every API replica and Celery worker writes to the same Redis, so the view is
  identical whichever replica serves the request.

Long-term trace retention belongs to the OTLP backend (Jaeger/Tempo/Langfuse).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from app.observability.logging import get_logger

_KEY_PREFIX = "agentverse:spans:"
DEFAULT_MAX_SPANS_PER_TENANT = 1000
DEFAULT_TTL_SECONDS = 24 * 3600
MAX_READ_LIMIT = 500

_log = get_logger(__name__)


def _attr_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_attr_value(v) for v in value]
    return str(value)


def span_to_record(span: ReadableSpan) -> dict[str, Any] | None:
    """Serialise a finished span, or None when it carries no tenant."""
    attrs = dict(span.attributes or {})
    tenant_id = attrs.get("agentverse.tenant_id")
    if not tenant_id:
        return None
    ctx = span.get_span_context()
    if ctx is None:
        return None
    parent = span.parent
    return {
        "name": span.name,
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "parent_span_id": format(parent.span_id, "016x") if parent is not None else None,
        "start_time": span.start_time,
        "end_time": span.end_time,
        "attributes": {k: _attr_value(v) for k, v in attrs.items()},
        "status": span.status.status_code.name,
    }


class RedisSpanStore:
    """Per-tenant capped list of recent spans in Redis (sync client).

    The sync client is used from the BatchSpanProcessor worker thread; the API
    route reads through ``asyncio.to_thread``.
    """

    def __init__(
        self,
        *,
        client: Any = None,
        redis_url: str | None = None,
        max_spans_per_tenant: int = DEFAULT_MAX_SPANS_PER_TENANT,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        if client is None and not redis_url:
            raise ValueError("RedisSpanStore needs a client or a redis_url")
        self._client = client
        self._redis_url = redis_url
        self._max = max(1, int(max_spans_per_tenant))
        self._ttl = max(1, int(ttl_seconds))

    @property
    def client(self) -> Any:
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(
                str(self._redis_url), socket_connect_timeout=2, socket_timeout=2
            )
        return self._client

    @staticmethod
    def key(tenant_id: str) -> str:
        return f"{_KEY_PREFIX}{tenant_id}"

    def add_many(self, records: Sequence[dict[str, Any]]) -> None:
        if not records:
            return
        by_tenant: dict[str, list[str]] = {}
        for rec in records:
            tenant_id = str(rec["attributes"]["agentverse.tenant_id"])
            by_tenant.setdefault(tenant_id, []).append(json.dumps(rec, default=str))
        pipe = self.client.pipeline(transaction=False)
        for tenant_id, payloads in by_tenant.items():
            key = self.key(tenant_id)
            pipe.lpush(key, *payloads)
            pipe.ltrim(key, 0, self._max - 1)
            pipe.expire(key, self._ttl)
        pipe.execute()

    def recent(self, tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Newest-first spans for one tenant. Raises on a Redis error."""
        n = max(1, min(int(limit), MAX_READ_LIMIT, self._max))
        raw = self.client.lrange(self.key(tenant_id), 0, n - 1)
        out: list[dict[str, Any]] = []
        for item in raw:
            try:
                out.append(json.loads(item))
            except (TypeError, ValueError):
                continue
        return out


class TenantSpanExporter(SpanExporter):
    """OTel exporter writing tenant-stamped spans into a ``RedisSpanStore``."""

    def __init__(self, store: RedisSpanStore) -> None:
        self._store = store

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        records = [r for r in (span_to_record(s) for s in spans) if r is not None]
        if not records:
            return SpanExportResult.SUCCESS
        try:
            self._store.add_many(records)
        except Exception as exc:
            _log.warning("span_store_export_failed", error=type(exc).__name__)
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True
