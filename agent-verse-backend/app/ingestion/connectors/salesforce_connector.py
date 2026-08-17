"""SalesforceConnector — Salesforce CRM data ingestion.

Uses Salesforce REST API with OAuth 2.0 (username-password or connected app).
Cursor: last record's SystemModstamp (ISO 8601).
Supports: any SObject (Account, Contact, Lead, Case, Opportunity, custom).
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


@register("salesforce", feature_flag="ingestion_connector_salesforce_enabled")
class SalesforceConnector(BaseConnector):
    """Salesforce CRM connector — SObjects via REST API and SOQL."""

    source_type = "salesforce"
    supports_acl_propagation = True

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time
        t0 = time.perf_counter()
        try:
            token, instance_url = await self._authenticate(config)
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{instance_url}/services/data/v58.0/",
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(ok=True, latency_ms=latency, metadata={"instance": instance_url})
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        import httpx

        from app.ingestion.source_config import RawDocument

        token, instance_url = await self._authenticate(config)
        cc = config.connection_config
        sobjects = cc.get("sobjects") or ["Account", "Contact", "Lead", "Case"]
        batch_size = int(cc.get("batch_size", 200))
        fields_map: dict = cc.get("fields") or {}
        headers = {"Authorization": f"Bearer {token}"}
        new_cursor = cursor or ""

        async with httpx.AsyncClient(timeout=30) as client:
            for sobject in sobjects:
                # Discover fields if not specified
                fields = fields_map.get(sobject) or await self._get_fields(client, instance_url, token, sobject)
                soql_fields = ", ".join(fields[:50])  # SOQL field limit
                soql = f"SELECT {soql_fields} FROM {sobject}"
                if cursor:
                    soql += f" WHERE SystemModstamp > {cursor}"
                soql += f" ORDER BY SystemModstamp ASC LIMIT {batch_size}"

                url: str | None = f"{instance_url}/services/data/v58.0/query"
                params: dict = {"q": soql}
                while url:
                    r = await client.get(url, params=params, headers=headers)
                    if not r.is_success:
                        _log.warning("salesforce: %s %d", sobject, r.status_code)
                        break
                    data = r.json()
                    for record in data.get("records", []):
                        modified = record.get("SystemModstamp", "")
                        new_cursor = max(new_cursor, modified)
                        record.pop("attributes", None)
                        text_parts = [f"{k}: {v}" for k, v in record.items() if v is not None]
                        text = f"SObject: {sobject}\n" + "\n".join(text_parts)
                        doc = RawDocument(
                            doc_id=str(uuid.uuid4()),
                            source_id=config.source_id, tenant_id=config.tenant_id,
                            source_url=f"{instance_url}/lightning/r/{sobject}/{record.get('Id')}/view",
                            content=text.encode(), content_type="text/plain",
                            metadata={"sobject": sobject, "id": record.get("Id"), "modified": modified},
                        )
                        yield doc, new_cursor
                    next_url = data.get("nextRecordsUrl")
                    url = f"{instance_url}{next_url}" if next_url else None
                    params = {}

    async def _authenticate(self, config: SourceConfig) -> tuple[str, str]:
        import httpx
        cc = config.connection_config
        login_url = cc.get("login_url", "https://login.salesforce.com")
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{login_url}/services/oauth2/token",
                data={
                    "grant_type": "password",
                    "client_id": cc.get("client_id", ""),
                    "client_secret": cc.get("client_secret", ""),
                    "username": cc.get("username", ""),
                    "password": cc.get("password", "") + cc.get("security_token", ""),
                },
            )
            r.raise_for_status()
            j = r.json()
            return j["access_token"], j["instance_url"]

    @staticmethod
    async def _get_fields(client, instance_url: str, token: str, sobject: str) -> list[str]:
        r = await client.get(
            f"{instance_url}/services/data/v58.0/sobjects/{sobject}/describe",
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.is_success:
            fields = r.json().get("fields", [])
            return [f["name"] for f in fields if f.get("type") not in ("base64", "encryptedstring")][:50]
        return ["Id", "Name", "CreatedDate", "SystemModstamp"]
