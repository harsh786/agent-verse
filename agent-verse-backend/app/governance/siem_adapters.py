"""SIEM integration adapters for the AgentVerse audit system.

Supported SIEM platforms:
- Splunk HTTP Event Collector (HEC)
- Elasticsearch Bulk API
- Datadog Logs API
- CEF (Common Event Format / ArcSight) via syslog UDP/TCP
- LEEF (Log Event Extended Format / QRadar) via HTTP
- Webhook (generic HTTP)
- Null (disabled / no-op)
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
import enum
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any, ClassVar

from app.observability.logging import get_logger

logger = get_logger(__name__)


class SIEMType(enum.StrEnum):
    SPLUNK = "splunk"
    ELASTICSEARCH = "elasticsearch"
    DATADOG = "datadog"
    CEF = "cef"
    LEEF = "leef"
    WEBHOOK = "webhook"
    NULL = "null"


@dataclass
class SIEMConfig:
    """Connection config for a SIEM adapter."""

    siem_type: SIEMType = SIEMType.WEBHOOK
    endpoint: str = ""
    api_key: str = ""
    index: str = "agentverse"
    source_type: str = "agentverse:audit"
    service: str = "agentverse"
    # CEF / LEEF syslog target
    host: str = ""
    port: int = 514
    protocol: str = "udp"
    extra: dict[str, Any] = field(default_factory=dict)


class SIEMAdapter(abc.ABC):
    """Abstract base class — every adapter exposes a single ``send`` coroutine.

    Direct instantiation raises ``TypeError`` at construction time, giving a clear
    error instead of a silent ``NotImplementedError`` at call time.
    """

    @abc.abstractmethod
    async def send(self, events: list[dict[str, Any]], config: SIEMConfig) -> bool:
        """Send events to the SIEM platform. Returns True on success."""


class NullSIEMAdapter(SIEMAdapter):
    """No-op adapter — used when SIEM is disabled or not configured.

    Always returns True (silently succeeds) without making any network calls.
    """

    async def send(self, events: list[dict[str, Any]], config: SIEMConfig) -> bool:
        return True


class SplunkHECAdapter(SIEMAdapter):
    """Splunk HTTP Event Collector adapter."""

    async def send(self, events: list[dict[str, Any]], config: SIEMConfig) -> bool:
        from datetime import datetime

        import httpx

        url = f"{config.endpoint.rstrip('/')}/services/collector/event"
        lines: list[str] = []
        for e in events:
            ts_raw = e.get("created_at", "")
            try:
                ts = int(datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp())
            except Exception:
                ts = 0
            lines.append(
                json.dumps(
                    {
                        "time": ts,
                        "index": config.index,
                        "sourcetype": config.source_type,
                        "event": e,
                    }
                )
            )
        payload = "\n".join(lines)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url,
                    content=payload,
                    headers={
                        "Authorization": f"Splunk {config.api_key}",
                        "Content-Type": "application/json",
                    },
                )
            return resp.status_code == 200
        except Exception as exc:
            logger.error("splunk_siem_send_error", error=str(exc))
            return False


class ElasticsearchAdapter(SIEMAdapter):
    """Elasticsearch Bulk API adapter."""

    async def send(self, events: list[dict[str, Any]], config: SIEMConfig) -> bool:
        import httpx

        url = f"{config.endpoint.rstrip('/')}/_bulk"
        lines: list[str] = []
        for e in events:
            lines.append(json.dumps({"index": {"_index": config.index, "_id": e.get("id", "")}}))
            lines.append(json.dumps(e))
        body = "\n".join(lines) + "\n"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url,
                    content=body,
                    headers={
                        "Authorization": f"ApiKey {config.api_key}",
                        "Content-Type": "application/x-ndjson",
                    },
                )
            return resp.status_code in (200, 201)
        except Exception as exc:
            logger.error("elasticsearch_siem_send_error", error=str(exc))
            return False


class DatadogAdapter(SIEMAdapter):
    """Datadog Logs API adapter."""

    DD_URL = "https://api.datadoghq.com/api/v2/logs"

    async def send(self, events: list[dict[str, Any]], config: SIEMConfig) -> bool:
        import httpx

        payload = [
            {
                "ddsource": "agentverse",
                "ddtags": (f"event_type:{e.get('event_type', '')},tenant:{e.get('tenant_id', '')}"),
                "hostname": "agentverse-agent",
                "service": config.service,
                "message": json.dumps(e),
            }
            for e in events
        ]
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    self.DD_URL,
                    json=payload,
                    headers={
                        "DD-API-KEY": config.api_key,
                        "Content-Type": "application/json",
                    },
                )
            return resp.status_code == 202
        except Exception as exc:
            logger.error("datadog_siem_send_error", error=str(exc))
            return False


class CEFAdapter(SIEMAdapter):
    """Common Event Format (ArcSight) adapter — syslog UDP or TCP."""

    _SEVERITY: ClassVar[dict[str, str]] = {
        "low": "3",
        "medium": "5",
        "high": "8",
        "critical": "10",
    }

    async def send(self, events: list[dict[str, Any]], config: SIEMConfig) -> bool:
        import socket

        sock_type = socket.SOCK_DGRAM if config.protocol == "udp" else socket.SOCK_STREAM
        try:
            with socket.socket(socket.AF_INET, sock_type) as sock:
                sock.settimeout(5.0)
                if config.protocol == "tcp":
                    sock.connect((config.host, config.port))
                for e in events:
                    sev = self._SEVERITY.get((e.get("metadata") or {}).get("severity", "low"), "3")
                    cef_line = (
                        f"CEF:0|AgentVerse|AgentVerse|1.0"
                        f"|{e.get('event_type', 'unknown')}"
                        f"|{e.get('action', 'unknown')}"
                        f"|{sev}|"
                        f"tenant={e.get('tenant_id', '')} "
                        f"resource={e.get('resource_type', '')} "
                        f"status={e.get('status', '')} "
                        f"requestId={e.get('request_id', '')}\n"
                    ).encode()
                    if config.protocol == "udp":
                        sock.sendto(cef_line, (config.host, config.port))
                    else:
                        sock.sendall(cef_line)
            return True
        except Exception as exc:
            logger.error("cef_siem_send_error", error=str(exc))
            return False


class LEEFAdapter(SIEMAdapter):
    """Log Event Extended Format (QRadar) adapter — HTTP endpoint."""

    LEEF_VERSION = "LEEF:2.0"
    VENDOR = "AgentVerse"
    PRODUCT = "AgentVerseOS"
    VERSION = "1.0"

    _SEVERITY: ClassVar[dict[str, str]] = {
        "low": "1",
        "medium": "5",
        "high": "8",
        "critical": "10",
    }

    async def send(self, events: list[dict[str, Any]], config: SIEMConfig) -> bool:
        from datetime import datetime

        import httpx

        lines: list[str] = []
        for e in events:
            event_id = (e.get("event_type") or "UNKNOWN").replace(".", "_").upper()
            header = f"{self.LEEF_VERSION}|{self.VENDOR}|{self.PRODUCT}|{self.VERSION}|{event_id}|"
            ts_raw = e.get("created_at", "")
            try:
                dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                dev_time = dt.strftime("%b %d %Y %H:%M:%S")
            except Exception:
                dev_time = ts_raw

            sev = self._SEVERITY.get((e.get("metadata") or {}).get("severity", "low"), "1")
            attrs = {
                "devTime": dev_time,
                "sev": sev,
                "src": e.get("ip_address") or "unknown",
                "usrName": e.get("actor_label") or "unknown",
                "resource": e.get("resource_type") or "",
                "action": e.get("action") or "",
                "outcome": e.get("status") or "",
                "tenantId": e.get("tenant_id") or "",
            }
            leef_line = header + "\t".join(f"{k}={v}" for k, v in attrs.items())
            lines.append(leef_line)

        body = "\n".join(lines)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    config.endpoint,
                    content=body.encode(),
                    headers={"Content-Type": "text/plain"},
                )
            return resp.status_code in (200, 201, 202)
        except Exception as exc:
            logger.error("leef_siem_send_error", error=str(exc))
            return False


class WebhookAdapter(SIEMAdapter):
    """Generic JSON webhook adapter."""

    async def send(self, events: list[dict[str, Any]], config: SIEMConfig) -> bool:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    config.endpoint,
                    json={"events": events},
                    headers={
                        "Authorization": f"Bearer {config.api_key}",
                        "Content-Type": "application/json",
                    },
                )
            return resp.status_code in (200, 201, 202, 204)
        except Exception as exc:
            logger.error("webhook_siem_send_error", error=str(exc))
            return False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

SIEM_ADAPTER_MAP: dict[SIEMType, type[SIEMAdapter]] = {
    SIEMType.SPLUNK: SplunkHECAdapter,
    SIEMType.ELASTICSEARCH: ElasticsearchAdapter,
    SIEMType.DATADOG: DatadogAdapter,
    SIEMType.CEF: CEFAdapter,
    SIEMType.LEEF: LEEFAdapter,
    SIEMType.WEBHOOK: WebhookAdapter,
    SIEMType.NULL: NullSIEMAdapter,
}


def build_siem_adapter(siem_type: str | SIEMType) -> SIEMAdapter:
    """Return a concrete SIEM adapter for the given type string or enum value."""
    if isinstance(siem_type, str):
        try:
            siem_type = SIEMType(siem_type)
        except ValueError:
            raise ValueError(f"Unknown SIEM type: {siem_type!r}") from None
    cls = SIEM_ADAPTER_MAP.get(siem_type)
    if cls is None:
        raise ValueError(f"Unknown SIEM type: {siem_type!r}")
    return cls()


# ---------------------------------------------------------------------------
# SIEMForwarder — batched, non-blocking audit → SIEM pump
# ---------------------------------------------------------------------------


class SIEMForwarder:
    """Buffers audit events and drains them to a :class:`SIEMAdapter` in batches.

    Design
    ------
    The audit write path must never block the HTTP response and must never
    raise, so :meth:`enqueue` only appends to a bounded in-memory buffer.  A
    background task started via :meth:`start` drains that buffer on an interval,
    calling ``adapter.send(batch, config)`` — the adapter's list-oriented
    interface — so events are shipped in batches rather than one network call
    per event.

    This is the "enqueue-on-write, async batched drain" model: it forwards the
    events that actually flow through :class:`~app.governance.audit.AuditLog`
    while keeping O(1) write latency.  A failed ``send`` is logged (the batch is
    dropped) rather than propagated, matching the audit trail's fire-and-forget
    contract.
    """

    def __init__(
        self,
        adapter: SIEMAdapter,
        config: SIEMConfig,
        *,
        batch_size: int = 100,
        flush_interval: float = 5.0,
        max_buffer: int = 10_000,
    ) -> None:
        self._adapter = adapter
        self._config = config
        self._batch_size = max(1, batch_size)
        self._flush_interval = flush_interval
        self._buffer: deque[dict[str, Any]] = deque(maxlen=max_buffer)
        self._task: asyncio.Task[None] | None = None
        self._stopped = False

    def enqueue(self, event: dict[str, Any]) -> None:
        """Buffer one audit event for forwarding. Non-blocking; never raises.

        When the buffer is full the oldest event is dropped (``deque(maxlen=...)``)
        so audit-heavy bursts can never exhaust memory or stall the caller.
        """
        try:
            self._buffer.append(event)
        except Exception as exc:  # pragma: no cover - defensive, deque.append is total
            logger.error("siem_enqueue_failed", error=str(exc))

    async def flush_once(self) -> int:
        """Send up to ``batch_size`` buffered events. Returns the count sent.

        Returns 0 when the buffer is empty or the adapter fails.
        """
        if not self._buffer:
            return 0
        batch: list[dict[str, Any]] = []
        while self._buffer and len(batch) < self._batch_size:
            batch.append(self._buffer.popleft())
        if not batch:
            return 0
        try:
            ok = await self._adapter.send(batch, self._config)
        except Exception as exc:
            logger.error("siem_forward_error", error=str(exc), count=len(batch))
            return 0
        if not ok:
            logger.warning("siem_forward_rejected", count=len(batch))
            return 0
        return len(batch)

    async def run(self) -> None:
        """Background loop: drain the buffer every ``flush_interval`` seconds."""
        while not self._stopped:
            try:
                while self._buffer:
                    if await self.flush_once() == 0:
                        break  # empty or adapter failing — wait for next interval
            except Exception as exc:  # pragma: no cover - loop must never die
                logger.error("siem_forwarder_loop_error", error=str(exc))
            await asyncio.sleep(self._flush_interval)

    def start(self) -> None:
        """Launch the background drain task (idempotent)."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        """Signal shutdown, cancel the task, and flush anything left buffered."""
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        # Best-effort final drain so shutdown doesn't lose buffered events.
        with contextlib.suppress(Exception):
            while self._buffer:
                if await self.flush_once() == 0:
                    break
