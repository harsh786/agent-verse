"""AzureBlobConnector — Azure Blob Storage ingestion.

Incremental: list blobs sorted by LastModified using cursor timestamp.
Supports all file formats via ParserRegistry dispatch.
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


@register("azure_blob", feature_flag="ingestion_connector_azure_blob_enabled")
class AzureBlobConnector(BaseConnector):
    """Azure Blob Storage ingestion connector."""

    source_type = "azure_blob"
    supports_deletion_tracking = True

    async def validate_connection(self, config: "SourceConfig") -> ConnectionHealth:
        import time
        t0 = time.perf_counter()
        try:
            from azure.storage.blob import BlobServiceClient  # type: ignore[import-not-found]
            conn_str = config.connection_config.get("connection_string", "")
            account_name = config.connection_config.get("account_name", "")
            account_key = config.connection_config.get("account_key", "")
            container = config.connection_config.get("container", "")

            if conn_str:
                client = BlobServiceClient.from_connection_string(conn_str)
            else:
                client = BlobServiceClient(
                    account_url=f"https://{account_name}.blob.core.windows.net",
                    credential=account_key,
                )
            cc = client.get_container_client(container)
            props = cc.get_container_properties()
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True, latency_ms=latency,
                metadata={"container": container, "lease_state": str(props.get("lease", {}).get("state"))},
            )
        except ImportError:
            return ConnectionHealth(ok=False, error="azure-storage-blob not installed")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: "SourceConfig", cursor: str | None
    ) -> AsyncIterator[tuple["RawDocument", str]]:
        from app.ingestion.source_config import RawDocument
        try:
            from azure.storage.blob import BlobServiceClient  # type: ignore[import-not-found]
        except ImportError:
            _log.error("azure-storage-blob not installed"); return

        conn_str = config.connection_config.get("connection_string", "")
        account_name = config.connection_config.get("account_name", "")
        account_key = config.connection_config.get("account_key", "")
        container = config.connection_config.get("container", "")
        prefix = config.connection_config.get("prefix", "") or None

        if conn_str:
            service = BlobServiceClient.from_connection_string(conn_str)
        else:
            service = BlobServiceClient(
                account_url=f"https://{account_name}.blob.core.windows.net",
                credential=account_key,
            )
        cc = service.get_container_client(container)

        new_cursor = cursor or ""
        for blob in cc.list_blobs(name_starts_with=prefix):
            if not self._matches(blob.name, config.include_patterns, config.exclude_patterns):
                continue
            blob_ts = blob.last_modified.isoformat() if blob.last_modified else ""
            if cursor and blob_ts <= cursor:
                continue
            try:
                data = cc.download_blob(blob.name).readall()
                doc = RawDocument(
                    doc_id=str(uuid.uuid4()),
                    source_id=config.source_id,
                    tenant_id=config.tenant_id,
                    source_url=f"https://{account_name}.blob.core.windows.net/{container}/{blob.name}",
                    content=data,
                    content_type=blob.content_settings.content_type or "application/octet-stream" if blob.content_settings else "application/octet-stream",
                    metadata={"container": container, "name": blob.name, "size": blob.size},
                )
                new_cursor = blob_ts or blob.name
                yield doc, new_cursor
            except Exception as exc:
                _log.warning("azure_blob: skip blob %s: %s", blob.name, exc)
