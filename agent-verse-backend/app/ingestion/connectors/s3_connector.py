"""S3Connector — AWS S3 / S3-compatible (MinIO, R2) ingestion.

Incremental: ListObjectsV2 with StartAfter cursor (LastModified-based).
Streaming: S3 event notifications via SQS or EventBridge.
Supports any file format via ParserRegistry dispatch.
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


@register("s3", feature_flag="ingestion_connector_s3_enabled")
class S3Connector(BaseConnector):
    """AWS S3 and S3-compatible object storage ingestion."""

    source_type = "s3"
    supports_streaming = True
    supports_deletion_tracking = True

    async def validate_connection(self, config: "SourceConfig") -> ConnectionHealth:
        import time
        t0 = time.perf_counter()
        try:
            import boto3  # type: ignore[import-not-found]
            bucket = config.connection_config.get("bucket", "")
            region = config.connection_config.get("region", "us-east-1")
            endpoint_url = config.connection_config.get("endpoint_url") or None
            credentials = config.connection_config.get("credentials", {})

            session = boto3.Session(
                aws_access_key_id=credentials.get("access_key_id"),
                aws_secret_access_key=credentials.get("secret_access_key"),
                region_name=region,
            )
            s3 = session.client("s3", endpoint_url=endpoint_url)
            # Quick check: head bucket
            s3.head_bucket(Bucket=bucket)

            latency = (time.perf_counter() - t0) * 1000
            # Estimate doc count
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=config.connection_config.get("prefix", ""), MaxKeys=1)
            key_count = resp.get("KeyCount", 0)
            return ConnectionHealth(
                ok=True,
                latency_ms=latency,
                metadata={"bucket": bucket, "region": region, "accessible_objects": key_count},
            )
        except ImportError:
            return ConnectionHealth(ok=False, error="boto3 not installed — pip install boto3")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: "SourceConfig", cursor: str | None
    ) -> AsyncIterator[tuple["RawDocument", str]]:
        """List S3 objects sorted by LastModified, yield those newer than cursor."""
        from app.ingestion.source_config import RawDocument

        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError:
            _log.error("boto3 not installed — cannot ingest from S3")
            return

        bucket = config.connection_config.get("bucket", "")
        prefix = config.connection_config.get("prefix", "")
        region = config.connection_config.get("region", "us-east-1")
        endpoint_url = config.connection_config.get("endpoint_url") or None
        credentials = config.connection_config.get("credentials", {})
        include_patterns = config.include_patterns
        exclude_patterns = config.exclude_patterns

        session = boto3.Session(
            aws_access_key_id=credentials.get("access_key_id"),
            aws_secret_access_key=credentials.get("secret_access_key"),
            region_name=region,
        )
        s3 = session.client("s3", endpoint_url=endpoint_url)

        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

        new_cursor = cursor or ""

        try:
            for page in pages:
                objects = sorted(
                    page.get("Contents", []),
                    key=lambda o: o["LastModified"].isoformat(),
                )
                for obj in objects:
                    key = obj["Key"]
                    last_modified = obj["LastModified"].isoformat()

                    # Skip objects older than cursor (already ingested)
                    if cursor and last_modified <= cursor:
                        continue

                    # Apply include/exclude patterns
                    if not self._matches_patterns(key, include_patterns, exclude_patterns):
                        continue

                    # Check file size
                    if obj.get("Size", 0) > config.max_doc_size_bytes:
                        _log.debug("s3_skip_too_large key=%s size=%d", key, obj["Size"])
                        continue

                    # Download object
                    try:
                        response = s3.get_object(Bucket=bucket, Key=key)
                        content_bytes = response["Body"].read()
                        content_type = response.get("ContentType", "application/octet-stream")
                    except Exception as e:
                        _log.warning("s3_download_error key=%s: %s", key, e)
                        continue

                    raw = RawDocument(
                        doc_id=f"s3://{bucket}/{key}",
                        source_id=config.source_id,
                        tenant_id=config.tenant_id,
                        content=content_bytes,
                        content_type=content_type,
                        source_url=f"s3://{bucket}/{key}",
                        title=key.split("/")[-1],
                        modified_at=last_modified,
                        metadata={"s3_key": key, "s3_bucket": bucket, "size": obj["Size"]},
                    )
                    if last_modified > new_cursor:
                        new_cursor = last_modified

                    yield raw, new_cursor

        except Exception as exc:
            _log.error("s3_connector_error bucket=%s: %s", bucket, exc)
            raise

    async def on_webhook(
        self,
        config: "SourceConfig",
        payload: bytes,
        headers: dict[str, str],
    ) -> AsyncIterator["RawDocument"]:
        """Handle S3 event notifications (SQS or EventBridge)."""
        import json
        from app.ingestion.source_config import RawDocument

        try:
            data = json.loads(payload)
        except Exception:
            return

        # Handle SQS-wrapped S3 events
        records = data.get("Records", [])
        for record in records:
            event_name = record.get("eventName", "")
            if "ObjectCreated" in event_name or "ObjectModified" in event_name:
                s3_info = record.get("s3", {})
                bucket = s3_info.get("bucket", {}).get("name", "")
                key = s3_info.get("object", {}).get("key", "")
                if bucket and key:
                    # Reuse get_delta for single-object fetch
                    async for raw, cursor in self._fetch_single(config, bucket, key):
                        yield raw

    async def _fetch_single(
        self, config: "SourceConfig", bucket: str, key: str
    ) -> AsyncIterator[tuple["RawDocument", str]]:
        """Fetch and yield a single S3 object."""
        from app.ingestion.source_config import RawDocument
        try:
            import boto3
            credentials = config.connection_config.get("credentials", {})
            s3 = boto3.client(
                "s3",
                aws_access_key_id=credentials.get("access_key_id"),
                aws_secret_access_key=credentials.get("secret_access_key"),
                region_name=config.connection_config.get("region", "us-east-1"),
                endpoint_url=config.connection_config.get("endpoint_url") or None,
            )
            response = s3.get_object(Bucket=bucket, Key=key)
            content_bytes = response["Body"].read()
            content_type = response.get("ContentType", "application/octet-stream")
            raw = RawDocument(
                doc_id=f"s3://{bucket}/{key}",
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                content=content_bytes,
                content_type=content_type,
                source_url=f"s3://{bucket}/{key}",
                title=key.split("/")[-1],
            )
            yield raw, key
        except Exception as exc:
            _log.warning("s3_fetch_single_error bucket=%s key=%s: %s", bucket, key, exc)

    def estimate_doc_count(self, config: "SourceConfig") -> int | None:
        try:
            import boto3
            credentials = config.connection_config.get("credentials", {})
            s3 = boto3.client(
                "s3",
                aws_access_key_id=credentials.get("access_key_id"),
                aws_secret_access_key=credentials.get("secret_access_key"),
                region_name=config.connection_config.get("region", "us-east-1"),
            )
            resp = s3.list_objects_v2(
                Bucket=config.connection_config.get("bucket", ""),
                Prefix=config.connection_config.get("prefix", ""),
                MaxKeys=1,
            )
            # S3 KeyCount is limited to page size; return as lower bound
            return resp.get("KeyCount")
        except Exception:
            return None

    @staticmethod
    def _matches_patterns(
        key: str,
        include: list[str],
        exclude: list[str],
    ) -> bool:
        """Check include/exclude glob/extension patterns."""
        import fnmatch
        if exclude:
            for pat in exclude:
                if fnmatch.fnmatch(key, pat):
                    return False
        if include:
            return any(fnmatch.fnmatch(key, pat) for pat in include)
        return True
