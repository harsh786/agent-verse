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

    MinIO endpoint defaults to the local infra endpoint.
    All S3 logic (ListObjectsV2 cursor, webhook, deletion tracking) is reused.
    """

    source_type = "minio"
    supports_streaming = True
    supports_deletion_tracking = True

    _DEFAULT_ENDPOINT = "http://localhost:9000"

    # Inject default endpoint_url for MinIO if not set
    async def _get_s3_client(self, config):  # type: ignore[override]
        if not config.connection_config.get("endpoint_url"):
            config.connection_config["endpoint_url"] = self._DEFAULT_ENDPOINT
        return await super()._get_s3_client(config) if hasattr(super(), "_get_s3_client") else None
