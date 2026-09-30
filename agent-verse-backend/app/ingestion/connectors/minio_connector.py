"""MinIOConnector — MinIO / S3-compatible object storage ingestion.

MinIO is already in the AgentVerse infra (infra/docker-compose.yml).
Reuses S3Connector logic with MinIO-specific defaults.
"""

from __future__ import annotations

from app.ingestion.connector_registry import register
from app.ingestion.connectors.s3_connector import S3Connector


@register("minio", feature_flag="ingestion_connector_minio_enabled")
class MinIOConnector(S3Connector):
    """MinIO S3-compatible object storage connector.

    ``endpoint_url`` is required (no default) and egress-guarded.
    All S3 logic (ListObjectsV2 cursor, webhook, deletion tracking) is reused.
    """

    source_type = "minio"
    supports_streaming = True
    supports_deletion_tracking = True

    # There is deliberately no default endpoint. The old default was the
    # platform's own MinIO (http://localhost:9000) — a tenant Source with no
    # endpoint_url would have indexed the platform's buckets into its own
    # knowledge base. (The override that injected it was never called, so the
    # default was dead code; it is removed rather than left armed.) endpoint_url
    # is required and egress-checked + pinned by S3Connector._pinned_endpoint.
    _requires_endpoint = True
