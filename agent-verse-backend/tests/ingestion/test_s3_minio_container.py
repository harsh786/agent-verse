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
