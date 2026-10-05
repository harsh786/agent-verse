"""GCSConnector — Google Cloud Storage ingestion.

Incremental: list objects sorted by updated (timeCreated/updated metadata).
Cursor: last object name (lexicographic) or last_updated timestamp string.
Supports all file formats via ParserRegistry dispatch.

google-cloud-storage is blocking: client construction, listing pages and
downloads run on the SDK pool (:mod:`app.ingestion.sdk_executor`).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorFetchError,
    ConnectorUnavailableError,
    describe_fetch_error,
    fetch_failure_document,
    is_retryable_status,
    stable_doc_id,
)
from app.ingestion.connector_registry import register
from app.ingestion.sdk_executor import iterate_blocking, run_blocking

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

# CONNECTOR_REPLAY_KEY "kind" of a failed GCS blob download (see replay_event).
_REPLAY_KIND = "gcs_blob"


def _make_client(storage: Any, creds_json: Any) -> Any:
    import json
    import os
    import tempfile

    if isinstance(creds_json, dict):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            json.dump(creds_json, tmp)
        try:
            return storage.Client.from_service_account_json(tmp.name)
        finally:
            os.unlink(tmp.name)
    return storage.Client()


@register("gcs", feature_flag="ingestion_connector_gcs_enabled")
class GCSConnector(BaseConnector):
    """Google Cloud Storage ingestion connector."""

    source_type = "gcs"
    supports_deletion_tracking = True

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            from google.cloud import storage  # type: ignore[import-not-found]

            creds_json = config.connection_config.get("service_account_json")
            bucket_name = config.connection_config.get("bucket", "")

            def _probe() -> tuple[bool, float, int]:
                client = _make_client(storage, creds_json)
                exists = client.bucket(bucket_name).exists()
                latency = (time.perf_counter() - t0) * 1000
                if not exists:
                    return False, latency, 0
                return True, latency, len(list(client.list_blobs(bucket_name, max_results=1)))

            exists, latency, sample = await run_blocking(_probe)
            if not exists:
                return ConnectionHealth(ok=False, error=f"bucket '{bucket_name}' not found")
            return ConnectionHealth(
                ok=True,
                latency_ms=latency,
                metadata={"bucket": bucket_name, "accessible": True, "sample_objects": sample},
            )
        except ImportError:
            return ConnectionHealth(ok=False, error="google-cloud-storage not installed")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def iter_live_doc_ids(self, config: SourceConfig) -> AsyncIterator[str]:
        """Stream every blob under the configured prefix/patterns (KB-44).

        ``list_blobs`` pages lazily; pages are pulled on the SDK pool and yielded
        as they arrive, so the bucket is never materialised in memory.
        """
        from google.cloud import storage

        creds_json = config.connection_config.get("service_account_json")
        bucket_name = config.connection_config.get("bucket", "")
        prefix = config.connection_config.get("prefix", "")
        client = await run_blocking(_make_client, storage, creds_json)
        blobs = client.list_blobs(bucket_name, prefix=prefix)
        async for blob in iterate_blocking(blobs, chunk_size=1000):
            if self._matches(blob.name, config.include_patterns, config.exclude_patterns):
                yield stable_doc_id(config, f"gs://{bucket_name}/{blob.name}")

    async def list_live_doc_ids(self, config: SourceConfig) -> set[str] | None:
        """The live listing as a set (small buckets / tests; reconciliation streams)."""
        return {doc_id async for doc_id in self.iter_live_doc_ids(config)}

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        try:
            from google.cloud import storage  # type: ignore[import-not-found]
        except ImportError as exc:
            # Returning nothing here reported a successful, empty sync.
            raise ConnectorUnavailableError(
                "google-cloud-storage is not installed on this server; the connector cannot run"
            ) from exc

        creds_json = config.connection_config.get("service_account_json")
        bucket_name = config.connection_config.get("bucket", "")
        prefix = config.connection_config.get("prefix", "")

        client = await run_blocking(_make_client, storage, creds_json)

        new_cursor = cursor or ""
        blobs = client.list_blobs(bucket_name, prefix=prefix)
        async for blob in iterate_blocking(blobs, chunk_size=100):
            if not self._matches(blob.name, config.include_patterns, config.exclude_patterns):
                continue
            blob_ts = blob.updated.isoformat() if blob.updated else ""
            if cursor and blob_ts <= cursor:
                continue
            try:
                content = await run_blocking(blob.download_as_bytes)
            except Exception as exc:
                # USR-1/USR-4: an unreadable blob is a counted failure (→ DLQ,
                # re-fetched by replay_event) — it used to be logged and skipped.
                _log.warning("gcs: cannot read blob %s: %s", blob.name, exc)
                code = getattr(exc, "code", None)
                yield fetch_failure_document(
                    config,
                    doc_id=stable_doc_id(config, f"gs://{bucket_name}/{blob.name}"),
                    reason=describe_fetch_error(exc),
                    retryable=is_retryable_status(code) if isinstance(code, int) else True,
                    source_url=f"gs://{bucket_name}/{blob.name}",
                    replay={"kind": _REPLAY_KIND, "bucket": bucket_name, "name": blob.name},
                    metadata={"bucket": bucket_name, "name": blob.name},
                ), new_cursor
                continue
            doc = RawDocument(
                doc_id=stable_doc_id(config, f"gs://{bucket_name}/{blob.name}"),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"gs://{bucket_name}/{blob.name}",
                content=content,
                content_type=blob.content_type or "application/octet-stream",
                metadata={"bucket": bucket_name, "name": blob.name, "size": blob.size},
            )
            new_cursor = blob_ts or blob.name
            yield doc, new_cursor

    async def replay_event(
        self, config: SourceConfig, reference: dict[str, Any]
    ) -> AsyncIterator[RawDocument]:
        """Download again a blob a sync could not read (DLQ retry, USR-4)."""
        from app.ingestion.source_config import RawDocument

        bucket_name = str(reference.get("bucket") or "")
        name = str(reference.get("name") or "")
        if reference.get("kind") != _REPLAY_KIND or not bucket_name or not name:
            raise ValueError(f"not a GCS blob replay reference: {reference!r}")
        if bucket_name != config.connection_config.get("bucket", ""):
            raise ConnectorFetchError(f"gcs: bucket {bucket_name!r} is not this Source's")
        from google.cloud import storage  # type: ignore[import-not-found]

        client = await run_blocking(
            _make_client, storage, config.connection_config.get("service_account_json")
        )
        blob = client.bucket(bucket_name).blob(name)
        content = await run_blocking(blob.download_as_bytes)
        yield RawDocument(
            doc_id=stable_doc_id(config, f"gs://{bucket_name}/{name}"),
            source_id=config.source_id,
            tenant_id=config.tenant_id,
            source_url=f"gs://{bucket_name}/{name}",
            content=content,
            content_type=getattr(blob, "content_type", None) or "application/octet-stream",
            metadata={"bucket": bucket_name, "name": name},
        )
