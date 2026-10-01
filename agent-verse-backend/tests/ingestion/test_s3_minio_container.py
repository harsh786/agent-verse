"""BLOCKING-SDK: S3 / MinIO connector against a real MinIO (testcontainers).

boto3 calls now run on the SDK pool (egress-checked, since the endpoint is the
tenant's); this proves validate, incremental listing + download, and the webhook
single-object fetch still work with the real client.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest

from app.ingestion.source_config import SourceConfig, SourceFamily

pytestmark = pytest.mark.integration

_KEY = "ingestminio"
_SECRET = "ingestminio-secret"


@pytest.fixture(scope="module")
def minio_endpoint() -> Iterator[str]:
    import boto3
    from testcontainers.core.container import DockerContainer

    container = (
        DockerContainer("minio/minio:latest")
        .with_command("server /data")
        .with_env("MINIO_ROOT_USER", _KEY)
        .with_env("MINIO_ROOT_PASSWORD", _SECRET)
        .with_exposed_ports(9000)
    )
    with container:
        endpoint = f"http://localhost:{container.get_exposed_port(9000)}"
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=_KEY,
            aws_secret_access_key=_SECRET,
            region_name="us-east-1",
        )
        for _ in range(60):
            try:
                s3.create_bucket(Bucket="docs")
                break
            except Exception:
                time.sleep(0.5)
        s3.put_object(Bucket="docs", Key="notes/a.txt", Body=b"hello from minio")
        yield endpoint


@pytest.fixture(autouse=True)
def _allow_localhost(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "localhost")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _config(endpoint: str) -> SourceConfig:
    cc: dict[str, Any] = {
        "bucket": "docs",
        "endpoint_url": endpoint,
        "credentials": {"access_key_id": _KEY, "secret_access_key": _SECRET},
    }
    return SourceConfig(
        source_id="src-minio",
        tenant_id="tenant-minio",
        name="minio",
        family=SourceFamily.OBJECT_STORAGE,
        source_type="minio",
        collection_id="col-1",
        connection_config=cc,
    )


async def test_validate_connection_with_real_boto3(minio_endpoint: str) -> None:
    from app.ingestion.connectors.minio_connector import MinIOConnector

    health = await MinIOConnector().validate_connection(_config(minio_endpoint))
    assert health.ok, health.error
    assert health.metadata["accessible_objects"] == 1


async def test_get_delta_with_real_boto3(minio_endpoint: str) -> None:
    from app.ingestion.connectors.minio_connector import MinIOConnector

    docs = [d async for d, _c in MinIOConnector().get_delta(_config(minio_endpoint), None)]
    assert [d.content for d in docs] == [b"hello from minio"]


async def test_webhook_fetch_with_real_boto3(minio_endpoint: str) -> None:
    from app.ingestion.connectors.minio_connector import MinIOConnector

    docs = [
        d
        async for d, _k in MinIOConnector()._fetch_single(
            _config(minio_endpoint), "docs", "notes/a.txt"
        )
    ]
    assert [d.content for d in docs] == [b"hello from minio"]


async def test_dlq_retry_replays_the_original_event_against_minio(minio_endpoint: str) -> None:
    """S3-DLQ-REPLAY end to end: a webhook fetch throttled once goes to the DLQ
    as a failure document carrying the event reference; the DLQ retry re-fetches
    the object from the real MinIO and indexes its content."""
    import dataclasses
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    import boto3
    from botocore.client import BaseClient
    from botocore.exceptions import ClientError

    from app.ingestion.connectors.minio_connector import MinIOConnector
    from app.ingestion.job_tracker import _json_default
    from app.ingestion.scheduler import _retry_one_dlq_entry
    from app.ingestion.source_config import CONNECTOR_FAILURE_KEY

    boto3.client(
        "s3",
        endpoint_url=minio_endpoint,
        aws_access_key_id=_KEY,
        aws_secret_access_key=_SECRET,
        region_name="us-east-1",
    ).put_object(Bucket="docs", Key="late.txt", Body=b"replayed from minio")

    config = _config(minio_endpoint)
    event = json.dumps(
        {
            "Records": [
                {
                    "eventName": "ObjectCreated:Put",
                    "s3": {"bucket": {"name": "docs"}, "object": {"key": "late.txt"}},
                }
            ]
        }
    ).encode()

    real_call = BaseClient._make_api_call

    def _throttle_get_object(self: Any, operation: str, params: dict[str, Any]) -> Any:
        if operation == "GetObject":
            raise ClientError(
                {"Error": {"Code": "SlowDown"}, "ResponseMetadata": {"HTTPStatusCode": 503}},
                operation,
            )
        return real_call(self, operation, params)

    with patch.object(BaseClient, "_make_api_call", _throttle_get_object):
        failed = [d async for d in MinIOConnector().on_webhook(config, event, {})]
    assert len(failed) == 1
    assert "SlowDown" in failed[0].metadata[CONNECTOR_FAILURE_KEY]

    # The DLQ row exactly as JobTracker.add_to_dlq serializes it.
    entry = {
        "dlq_id": "dlq-1",
        "tenant_id": config.tenant_id,
        "source_id": config.source_id,
        "doc_id": failed[0].doc_id,
        "retry_count": 0,
        "raw_doc_json": json.dumps(dataclasses.asdict(failed[0]), default=_json_default),
    }
    indexed: list[Any] = []

    async def _run(doc: Any, *, source_config: Any) -> Any:
        indexed.append(doc)
        return SimpleNamespace(success=True, skipped=False, skip_reason="", error="")

    pipeline = SimpleNamespace(run=_run)
    tracker = MagicMock()
    tracker.resolve_dlq_entry = AsyncMock()
    tracker.increment_dlq_retry = AsyncMock()
    tracker.mark_dlq_permanent_failure = AsyncMock()
    store = SimpleNamespace(get=AsyncMock(return_value=config))

    outcome = await _retry_one_dlq_entry(entry, tracker, pipeline, store)
    assert outcome == "succeeded"
    assert [d.content for d in indexed] == [b"replayed from minio"]
    tracker.resolve_dlq_entry.assert_awaited_once_with("dlq-1", config.tenant_id)
