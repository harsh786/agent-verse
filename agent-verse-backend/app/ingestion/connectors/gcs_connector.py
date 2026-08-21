"""GCSConnector — Google Cloud Storage ingestion.

Incremental: list objects sorted by updated (timeCreated/updated metadata).
Cursor: last object name (lexicographic) or last_updated timestamp string.
Supports all file formats via ParserRegistry dispatch.
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

            import json
            import os
            import tempfile

            if isinstance(creds_json, dict):
                tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
                json.dump(creds_json, tmp)
                tmp.close()
                client = storage.Client.from_service_account_json(tmp.name)
                os.unlink(tmp.name)
            else:
                client = storage.Client()

            bucket = client.bucket(bucket_name)
            exists = bucket.exists()
            latency = (time.perf_counter() - t0) * 1000
            if not exists:
                return ConnectionHealth(ok=False, error=f"bucket '{bucket_name}' not found")
            blobs = list(client.list_blobs(bucket_name, max_results=1))
            return ConnectionHealth(
                ok=True,
                latency_ms=latency,
                metadata={"bucket": bucket_name, "accessible": True, "sample_objects": len(blobs)},
            )
        except ImportError:
            return ConnectionHealth(ok=False, error="google-cloud-storage not installed")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        try:
            from google.cloud import storage  # type: ignore[import-not-found]
        except ImportError:
            _log.error("google-cloud-storage not installed")
            return

        import json
        import os
        import tempfile

        creds_json = config.connection_config.get("service_account_json")
        bucket_name = config.connection_config.get("bucket", "")
        prefix = config.connection_config.get("prefix", "")

        if isinstance(creds_json, dict):
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
            json.dump(creds_json, tmp)
            tmp.close()
            client = storage.Client.from_service_account_json(tmp.name)
            os.unlink(tmp.name)
        else:
            client = storage.Client()

        new_cursor = cursor or ""
        for blob in client.list_blobs(bucket_name, prefix=prefix):
            if not self._matches(blob.name, config.include_patterns, config.exclude_patterns):
                continue
            blob_ts = blob.updated.isoformat() if blob.updated else ""
            if cursor and blob_ts <= cursor:
                continue
            try:
                content = blob.download_as_bytes()
                doc = RawDocument(
                    doc_id=str(uuid.uuid4()),
                    source_id=config.source_id,
                    tenant_id=config.tenant_id,
                    source_url=f"gs://{bucket_name}/{blob.name}",
                    content=content,
                    content_type=blob.content_type or "application/octet-stream",
                    metadata={"bucket": bucket_name, "name": blob.name, "size": blob.size},
                )
                new_cursor = blob_ts or blob.name
                yield doc, new_cursor
            except Exception as exc:
                _log.warning("gcs: skip blob %s: %s", blob.name, exc)
