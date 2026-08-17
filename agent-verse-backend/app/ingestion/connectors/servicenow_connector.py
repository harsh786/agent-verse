"""ServiceNowConnector — ServiceNow ITSM incidents, problems, and KB articles.

Cursor: last record's sys_updated_on timestamp.
Uses ServiceNow Table API.
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


@register("servicenow", feature_flag="ingestion_connector_servicenow_enabled")
class ServiceNowConnector(BaseConnector):
    """ServiceNow connector — incidents, problems, changes, KB via Table API."""

    source_type = "servicenow"

    async def validate_connection(self, config: "SourceConfig") -> ConnectionHealth:
        import time, httpx
        t0 = time.perf_counter()
        try:
            cc = config.connection_config
            instance = cc.get("instance", "").rstrip("/")
            auth = (cc.get("username", ""), cc.get("password", ""))
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"https://{instance}.service-now.com/api/now/table/sys_user_role",
                    params={"sysparm_limit": 1},
                    auth=auth,
                    headers={"Accept": "application/json"},
                )
                r.raise_for_status()
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(ok=True, latency_ms=latency, metadata={"instance": instance})
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: "SourceConfig", cursor: str | None
    ) -> AsyncIterator[tuple["RawDocument", str]]:
        from app.ingestion.source_config import RawDocument
        import httpx

        cc = config.connection_config
        instance = cc.get("instance", "").rstrip("/")
        auth = (cc.get("username", ""), cc.get("password", ""))
        tables = cc.get("tables") or ["incident", "problem", "change_request", "kb_knowledge"]
        batch_size = int(cc.get("batch_size", 100))
        base = f"https://{instance}.service-now.com/api/now/table"
        headers = {"Accept": "application/json"}
        new_cursor = cursor or ""

        async with httpx.AsyncClient(timeout=30) as client:
            for table in tables:
                params: dict = {
                    "sysparm_limit": batch_size,
                    "sysparm_order": "sys_updated_on ASC",
                }
                if cursor:
                    params["sysparm_query"] = f"sys_updated_on>{cursor}"

                url: str | None = f"{base}/{table}"
                while url:
                    r = await client.get(url, params=params, auth=auth, headers=headers)
                    if not r.is_success:
                        _log.warning("servicenow: %s %d", table, r.status_code)
                        break
                    records = r.json().get("result", [])
                    if not records:
                        break
                    for rec in records:
                        updated = rec.get("sys_updated_on", "")
                        new_cursor = max(new_cursor, updated)
                        short_desc = rec.get("short_description") or rec.get("title") or rec.get("name") or ""
                        body = rec.get("description") or rec.get("text") or rec.get("work_notes") or ""
                        sys_id = rec.get("sys_id", "")
                        number = rec.get("number") or sys_id
                        text = f"[{table.upper()}] {number}: {short_desc}\nUpdated: {updated}\n\n{body}"
                        doc = RawDocument(
                            doc_id=str(uuid.uuid4()),
                            source_id=config.source_id, tenant_id=config.tenant_id,
                            source_url=f"https://{instance}.service-now.com/nav_to.do?source_url={table}.do?sys_id={sys_id}",
                            content=text.encode(), content_type="text/plain",
                            metadata={"table": table, "sys_id": sys_id, "number": number, "updated": updated},
                        )
                        yield doc, new_cursor
                    # Next page: use last record's updated time
                    if len(records) < batch_size:
                        break
                    last_ts = records[-1].get("sys_updated_on", "")
                    if last_ts:
                        params = {"sysparm_limit": batch_size, "sysparm_order": "sys_updated_on ASC", "sysparm_query": f"sys_updated_on>{last_ts}"}
                    else:
                        break
