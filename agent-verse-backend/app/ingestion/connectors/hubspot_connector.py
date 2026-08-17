"""HubSpotConnector — HubSpot CRM contacts, companies, deals, and tickets.

Uses HubSpot REST API v3. Cursor: last record's updatedAt timestamp.
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
_HUBSPOT_BASE = "https://api.hubapi.com/crm/v3/objects"


@register("hubspot", feature_flag="ingestion_connector_hubspot_enabled")
class HubSpotConnector(BaseConnector):
    """HubSpot CRM connector — contacts, companies, deals, tickets."""

    source_type = "hubspot"

    async def validate_connection(self, config: "SourceConfig") -> ConnectionHealth:
        import time, httpx
        t0 = time.perf_counter()
        try:
            token = config.connection_config.get("access_token", "")
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.hubapi.com/crm/v3/objects/contacts",
                    params={"limit": 1},
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(ok=True, latency_ms=latency, metadata={"api": "hubspot"})
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: "SourceConfig", cursor: str | None
    ) -> AsyncIterator[tuple["RawDocument", str]]:
        from app.ingestion.source_config import RawDocument
        import httpx

        cc = config.connection_config
        token = cc.get("access_token", "")
        object_types = cc.get("object_types") or ["contacts", "companies", "deals", "tickets"]
        batch_size = min(int(cc.get("batch_size", 100)), 100)
        headers = {"Authorization": f"Bearer {token}"}
        new_cursor = cursor or ""

        async with httpx.AsyncClient(timeout=30) as client:
            for obj_type in object_types:
                params: dict = {"limit": batch_size, "sort": "updatedAt"}
                if cursor:
                    params["after"] = cursor  # paging token; not ideal for time but best available

                url: str | None = f"{_HUBSPOT_BASE}/{obj_type}"
                while url:
                    r = await client.get(url, params=params, headers=headers)
                    if not r.is_success: break
                    data = r.json()
                    for result in data.get("results", []):
                        props = result.get("properties", {})
                        updated = props.get("hs_lastmodifieddate") or props.get("updatedAt", "")
                        new_cursor = max(new_cursor, updated)
                        text_parts = [f"{k}: {v}" for k, v in props.items() if v]
                        text = f"HubSpot {obj_type.rstrip('s').capitalize()}: {result.get('id')}\n" + "\n".join(text_parts)
                        doc = RawDocument(
                            doc_id=str(uuid.uuid4()),
                            source_id=config.source_id, tenant_id=config.tenant_id,
                            source_url=f"https://app.hubspot.com/contacts/{result.get('id')}",
                            content=text.encode(), content_type="text/plain",
                            metadata={"object_type": obj_type, "id": result.get("id"), "updated": updated},
                        )
                        yield doc, new_cursor
                    paging = data.get("paging", {}).get("next", {}).get("after")
                    if paging:
                        params = {"limit": batch_size, "sort": "updatedAt", "after": paging}
                    else:
                        url = None
