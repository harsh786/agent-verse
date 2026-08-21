"""PagerDutyConnector — PagerDuty incident and postmortem ingestion.

Cursor: last incident created_at timestamp (ISO 8601).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)
_PD_BASE = "https://api.pagerduty.com"


@register("pagerduty", feature_flag="ingestion_connector_pagerduty_enabled")
class PagerDutyConnector(BaseConnector):
    """PagerDuty connector — incidents, alerts, postmortems, and notes."""

    source_type = "pagerduty"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        import httpx

        t0 = time.perf_counter()
        try:
            token = config.connection_config.get("api_token", "")
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{_PD_BASE}/users/me",
                    headers={
                        "Authorization": f"Token token={token}",
                        "Accept": "application/vnd.pagerduty+json;version=2",
                    },
                )
                r.raise_for_status()
                user = r.json().get("user", {})
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True,
                latency_ms=latency,
                metadata={"user": user.get("name"), "email": user.get("email")},
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        import httpx

        from app.ingestion.source_config import RawDocument

        cc = config.connection_config
        token = cc.get("api_token", "")
        ingest_types = cc.get("ingest_types") or ["incidents"]
        headers = {
            "Authorization": f"Token token={token}",
            "Accept": "application/vnd.pagerduty+json;version=2",
        }
        batch_size = min(int(cc.get("batch_size", 100)), 100)
        new_cursor = cursor or ""

        async with httpx.AsyncClient(timeout=30) as client:
            if "incidents" in ingest_types:
                params: dict = {"limit": batch_size, "sort_by": "created_at:asc", "total": "false"}
                if cursor:
                    params["since"] = cursor
                offset = 0
                while True:
                    params["offset"] = offset
                    r = await client.get(f"{_PD_BASE}/incidents", params=params, headers=headers)
                    if not r.is_success:
                        break
                    data = r.json()
                    incidents = data.get("incidents", [])
                    if not incidents:
                        break
                    for incident in incidents:
                        created = incident.get("created_at", "")
                        new_cursor = max(new_cursor, created)
                        status = incident.get("status", "")
                        urgency = incident.get("urgency", "")
                        service = incident.get("service", {}).get("summary", "")
                        text = (
                            f"PagerDuty Incident: {incident.get('title', '')}\n"
                            f"ID: {incident.get('id')}  Status: {status}  Urgency: {urgency}\n"
                            f"Service: {service}  Created: {created}\n\n"
                            f"{incident.get('description') or incident.get('summary') or ''}"
                        )
                        doc = RawDocument(
                            doc_id=str(uuid.uuid4()),
                            source_id=config.source_id,
                            tenant_id=config.tenant_id,
                            source_url=incident.get("html_url", ""),
                            content=text.encode(),
                            content_type="text/plain",
                            metadata={
                                "id": incident.get("id"),
                                "status": status,
                                "urgency": urgency,
                                "created": created,
                            },
                        )
                        yield doc, new_cursor
                    if not data.get("more"):
                        break
                    offset += batch_size
