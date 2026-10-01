"""AzureBlobConnector — Azure Blob Storage ingestion.

Incremental: list blobs sorted by LastModified using cursor timestamp.
Supports all file formats via ParserRegistry dispatch.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorUnavailableError,
)
from app.ingestion.connector_egress import (
    ConnectorEgressBlockedError,
    pin_source_urls,
    run_driver_call,
)
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


def _download_capped(container_client: Any, name: str, cap: int) -> bytes | None:
    """Stream blob *name*; its bytes, or None once it exceeds *cap* bytes."""
    downloader = container_client.download_blob(name, offset=0, length=cap + 1)
    buf = bytearray()
    for chunk in downloader.chunks():
        buf.extend(chunk)
        if len(buf) > cap:
            return None
    return bytes(buf)


def _pinned_azure_egress(config: SourceConfig) -> Any:
    """Egress-check every endpoint the SDK could dial and pin them for the block.

    Raises ConnectorEgressBlockedError for an internal / unparseable endpoint.
    Inside the block the SDK's own lookups of those hosts answer with the checked
    addresses only (no DNS-rebinding window between check and connect).
    """
    return pin_source_urls(azure_blob_endpoints(config.connection_config), context="azure_blob")


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
            async with _pinned_azure_egress(config):
                from azure.storage.blob import BlobServiceClient  # type: ignore[import-not-found]

                conn_str = config.connection_config.get("connection_string", "")
                account_name = config.connection_config.get("account_name", "")
                account_key = config.connection_config.get("account_key", "")
                container = config.connection_config.get("container", "")

                def _properties() -> Any:
                    if conn_str:
                        client = BlobServiceClient.from_connection_string(conn_str)
                    else:
                        client = BlobServiceClient(
                            account_url=f"https://{account_name}.blob.core.windows.net",
                            credential=account_key,
                        )
                    return client.get_container_client(container).get_container_properties()

                # Blocking SDK: off the event loop, and a redirect is egress-checked.
                props = await run_driver_call(_properties, context="azure_blob")
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

        # Raises ConnectorEgressBlockedError for an internal / unparseable endpoint.
        async with _pinned_azure_egress(config):
            async for item in self._iter_blobs(config, cursor):
                yield item

    async def _iter_blobs(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        """Runs inside the pinned egress block (see :func:`_pinned_azure_egress`).

        Blobs are processed in ``last_modified`` order and the cursor is the
        timestamp of the last blob read successfully *before* any failure: a
        blob that could not be downloaded is reported (the pipeline fails it
        with the reason → DLQ) and the cursor never moves past it, so the next
        sync retries it. An oversized blob is reported too (retrying cannot
        help, so it does not hold the cursor). SDK calls run in a worker thread,
        egress-checked: azure-core follows redirects, and a redirect target is a
        host nobody pinned.
        """
        from app.ingestion.source_config import CONNECTOR_FAILURE_KEY, RawDocument

        try:
            from azure.storage.blob import BlobServiceClient  # type: ignore[import-not-found]
        except ImportError as exc:
            # Returning nothing reported a successful, empty sync.
            raise ConnectorUnavailableError(
                "azure-storage-blob is not installed; the Azure Blob connector cannot run"
            ) from exc

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

        def _list() -> list[Any]:
            return list(cc.list_blobs(name_starts_with=prefix))

        pending: list[tuple[str, Any]] = []
        for blob in await run_driver_call(_list, context="azure_blob"):
            if not self._matches(blob.name, config.include_patterns, config.exclude_patterns):
                continue
            blob_ts = blob.last_modified.isoformat() if blob.last_modified else ""
            if cursor and blob_ts <= cursor:
                continue
            pending.append((blob_ts, blob))
        pending.sort(key=lambda item: (item[0], item[1].name))

        cap = int(config.max_doc_size_bytes)
        new_cursor = cursor or ""
        read_ok: list[str] = []  # timestamps of blobs read successfully, in order
        held_below: str | None = None  # earliest timestamp of a failed blob

        def _advance(blob_ts: str) -> str:
            read_ok.append(blob_ts)
            if held_below is None:
                return max(new_cursor, blob_ts)
            below = [ts for ts in read_ok if ts < held_below]
            return max([cursor or "", *below])

        def _report(blob: Any, reason: str) -> RawDocument:
            return RawDocument(
                doc_id=str(uuid.uuid4()),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"https://{account_name}.blob.core.windows.net/{container}/{blob.name}",
                content=b"",
                content_type="application/octet-stream",
                metadata={
                    "container": container,
                    "name": blob.name,
                    "size": getattr(blob, "size", None),
                    CONNECTOR_FAILURE_KEY: reason,
                },
            )

        for blob_ts, blob in pending:
            # Bounded download: skip an oversized blob by its declared size and
            # stream the rest — at most cap + 1 bytes — aborting past the cap.
            declared = getattr(blob, "size", None)
            too_big = isinstance(declared, int) and declared > cap
            data: bytes | None = None
            if not too_big:
                try:
                    data = await run_driver_call(
                        _download_capped, cc, blob.name, cap, context="azure_blob"
                    )
                except Exception as exc:
                    _log.warning("azure_blob: download failed for blob %s: %s", blob.name, exc)
                    if held_below is None or blob_ts < held_below:
                        held_below = blob_ts
                    below = [ts for ts in read_ok if ts < held_below]
                    new_cursor = max([cursor or "", *below])
                    yield _report(blob, f"download failed: {str(exc)[:300]}"), new_cursor
                    continue
                too_big = data is None
            if too_big:
                new_cursor = _advance(blob_ts)
                yield _report(blob, f"blob exceeds the {cap}-byte size cap"), new_cursor
                continue
            doc = RawDocument(
                doc_id=str(uuid.uuid4()),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"https://{account_name}.blob.core.windows.net/{container}/{blob.name}",
                content=data or b"",
                content_type=blob.content_settings.content_type or "application/octet-stream"
                if blob.content_settings
                else "application/octet-stream",
                metadata={"container": container, "name": blob.name, "size": blob.size},
            )
            new_cursor = _advance(blob_ts)
            yield doc, new_cursor
