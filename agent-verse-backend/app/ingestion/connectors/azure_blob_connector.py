"""AzureBlobConnector — Azure Blob Storage ingestion.

Incremental: list blobs sorted by LastModified using cursor timestamp.
Supports all file formats via ParserRegistry dispatch.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_egress import ConnectorEgressBlockedError, assert_source_url
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

# Azure storage account names: 3-24 lowercase letters/digits. Anything else
# (``10.0.0.5/``, ``x@127.0.0.1#``) could rewrite the host of the derived URL.
_ACCOUNT_NAME = re.compile(r"^[a-z0-9]{3,24}$")
_ENDPOINT_SUFFIX = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


def _parse_connection_string(conn_str: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for segment in conn_str.split(";"):
        key, sep, value = segment.strip().partition("=")
        if sep:
            parts[key.strip().lower()] = value.strip()
    return parts


def _account_url(account_name: str, *, protocol: str = "https", suffix: str = "") -> str:
    if not _ACCOUNT_NAME.match(account_name or ""):
        raise ConnectorEgressBlockedError(
            f"SSRF guard [azure_blob]: invalid storage account name {account_name!r}"
        )
    suffix = suffix or "core.windows.net"
    if not _ENDPOINT_SUFFIX.match(suffix):
        raise ConnectorEgressBlockedError(
            f"SSRF guard [azure_blob]: invalid endpoint suffix {suffix!r}"
        )
    scheme = (protocol or "https").lower()
    if scheme not in ("http", "https"):
        raise ConnectorEgressBlockedError(f"SSRF guard [azure_blob]: invalid protocol {scheme!r}")
    return f"{scheme}://{account_name}.blob.{suffix}"


def azure_blob_endpoints(connection_config: Mapping[str, Any]) -> list[str]:
    """Every endpoint the Azure SDK could dial for this config (not yet checked).

    ``BlobServiceClient.from_connection_string`` honours ``BlobEndpoint``,
    ``BlobSecondaryEndpoint``, ``UseDevelopmentStorage`` (127.0.0.1:10000) and
    ``DevelopmentStorageProxyUri``, or derives ``<AccountName>.blob.<EndpointSuffix>``.
    Development storage is refused outright; every URL-valued key is returned
    so none can slip past the check.
    """
    conn_str = str(connection_config.get("connection_string", "") or "")
    if not conn_str:
        return [_account_url(str(connection_config.get("account_name", "") or ""))]

    parts = _parse_connection_string(conn_str)
    if parts.get("usedevelopmentstorage", "").lower() == "true" or (
        "developmentstorageproxyuri" in parts
    ):
        raise ConnectorEgressBlockedError(
            "SSRF guard [azure_blob]: development storage (loopback emulator) is blocked"
        )
    endpoints: list[str] = []
    if parts.get("blobendpoint"):
        endpoints.append(parts["blobendpoint"])
    elif parts.get("accountname"):
        endpoints.append(
            _account_url(
                parts["accountname"],
                protocol=parts.get("defaultendpointsprotocol", "https"),
                suffix=parts.get("endpointsuffix", ""),
            )
        )
    # Any other URL-valued key (secondary endpoints, proxies, ...).
    for key, value in parts.items():
        url_valued = "://" in value or (key.endswith("endpoint") and bool(value))
        if url_valued and value not in endpoints:
            endpoints.append(value)
    if not endpoints:
        raise ConnectorEgressBlockedError(
            "SSRF guard [azure_blob]: connection string names no blob endpoint"
        )
    return endpoints


def _assert_azure_egress(config: SourceConfig) -> None:
    for endpoint in azure_blob_endpoints(config.connection_config):
        assert_source_url(endpoint, context="azure_blob", config=config)


@register("azure_blob", feature_flag="ingestion_connector_azure_blob_enabled")
class AzureBlobConnector(BaseConnector):
    """Azure Blob Storage ingestion connector."""

    source_type = "azure_blob"
    supports_deletion_tracking = True

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            # Egress check before the SDK is even imported: the connection
            # string / account name is tenant-controlled.
            await asyncio.to_thread(_assert_azure_egress, config)
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
                ok=True,
                latency_ms=latency,
                metadata={
                    "container": container,
                    "lease_state": str(props.get("lease", {}).get("state")),
                },
            )
        except ImportError:
            return ConnectionHealth(ok=False, error="azure-storage-blob not installed")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        # Raises ConnectorEgressBlockedError for an internal / unparseable endpoint.
        await asyncio.to_thread(_assert_azure_egress, config)
        try:
            from azure.storage.blob import BlobServiceClient  # type: ignore[import-not-found]
        except ImportError:
            _log.error("azure-storage-blob not installed")
            return

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
                    content_type=blob.content_settings.content_type or "application/octet-stream"
                    if blob.content_settings
                    else "application/octet-stream",
                    metadata={"container": container, "name": blob.name, "size": blob.size},
                )
                new_cursor = blob_ts or blob.name
                yield doc, new_cursor
            except Exception as exc:
                _log.warning("azure_blob: skip blob %s: %s", blob.name, exc)
