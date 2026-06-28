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
import enum
import json
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
                ts = int(
                    datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
                )
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
            lines.append(
                json.dumps({"index": {"_index": config.index, "_id": e.get("id", "")}})
            )
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
                "ddtags": (
                    f"event_type:{e.get('event_type', '')},"
                    f"tenant:{e.get('tenant_id', '')}"
                ),
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

        sock_type = (
            socket.SOCK_DGRAM if config.protocol == "udp" else socket.SOCK_STREAM
        )
        try:
            with socket.socket(socket.AF_INET, sock_type) as sock:
                sock.settimeout(5.0)
                if config.protocol == "tcp":
                    sock.connect((config.host, config.port))
                for e in events:
                    sev = self._SEVERITY.get(
                        (e.get("metadata") or {}).get("severity", "low"), "3"
                    )
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
            event_id = (
                (e.get("event_type") or "UNKNOWN").replace(".", "_").upper()
            )
            header = (
                f"{self.LEEF_VERSION}|{self.VENDOR}|{self.PRODUCT}"
                f"|{self.VERSION}|{event_id}|"
            )
            ts_raw = e.get("created_at", "")
            try:
                dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                dev_time = dt.strftime("%b %d %Y %H:%M:%S")
            except Exception:
                dev_time = ts_raw

            sev = self._SEVERITY.get(
                (e.get("metadata") or {}).get("severity", "low"), "1"
            )
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
