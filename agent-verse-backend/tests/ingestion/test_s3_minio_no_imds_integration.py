"""BUG A against a real MinIO (testcontainers): a sync never touches IMDS.

The worker's S3 sync on the owner's cluster died with ``SSRF guard [s3]: metadata
service hostname '169.254.169.254' blocked``: credentials the worker could not use
turned into ``aws_access_key_id=None`` and botocore walked its default chain to the
EC2 instance metadata service. Here a real sync (ListObjectsV2 + GetObject), the
single-object fetch and the count estimate run against a real MinIO, through the
connector, with every IMDS / default-chain lookup forced to fail and the process
env carrying a different ("platform") AWS identity:

* with the Source's keys — signed with those keys only;
* without keys, on an anonymously readable bucket — explicitly UNSIGNED;
* with keys this process cannot decrypt — refused with the reason, nothing sent.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import pytest

from app.ingestion.source_config import SourceConfig, SourceFamily

pytestmark = pytest.mark.integration

_KEY = "imdsminio"
_SECRET = "imdsminio-secret-value"


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
        admin = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=_KEY,
            aws_secret_access_key=_SECRET,
            region_name="us-east-1",
        )
        for _ in range(60):
            try:
                admin.create_bucket(Bucket="private")
                break
            except Exception:
                time.sleep(0.5)
        admin.create_bucket(Bucket="public")
        admin.put_bucket_policy(
            Bucket="public",
            Policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"AWS": ["*"]},
                            "Action": ["s3:GetObject", "s3:ListBucket"],
                            "Resource": ["arn:aws:s3:::public", "arn:aws:s3:::public/*"],
                        }
                    ],
                }
            ),
        )
        admin.put_object(Bucket="private", Key="docs/a.txt", Body=b"private tenant data")
        admin.put_object(Bucket="public", Key="docs/p.txt", Body=b"public data")
        yield endpoint


def _boom(what: str) -> Any:
    def _raise(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError(f"{what} was consulted for a tenant client")

    return _raise


@pytest.fixture(autouse=True)
def _no_imds(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Allow the local MinIO; force every IMDS / ambient-chain lookup to fail."""
    import botocore.credentials
    import botocore.utils

    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "localhost")
    # A different ambient identity in the env: must never sign a tenant request.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAPLATFORMAMBIENT")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "platform-ambient-secret")
    monkeypatch.setattr(botocore.utils.InstanceMetadataFetcher, "_fetch_metadata_token",
                        _boom("IMDS (token)"))
    monkeypatch.setattr(botocore.utils.InstanceMetadataFetcher, "_get_request",
                        _boom("IMDS (request)"))
    monkeypatch.setattr(botocore.credentials.InstanceMetadataProvider, "load",
                        _boom("the instance-metadata credential provider"))
    monkeypatch.setattr(botocore.credentials.EnvProvider, "load",
                        _boom("the environment credential provider"))
    monkeypatch.setattr(botocore.credentials, "create_credential_resolver",
                        _boom("botocore's default credential chain"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _config(endpoint: str, bucket: str, **cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id=f"src-{bucket}",
        tenant_id="tenant-imds",
        name=bucket,
        family=SourceFamily.OBJECT_STORAGE,
        source_type="minio",
        collection_id="col-1",
        connection_config={"bucket": bucket, "endpoint_url": endpoint, **cc},
    )


_CREDS = {"credentials": {"access_key_id": _KEY, "secret_access_key": _SECRET}}


async def test_signed_sync_list_and_get_object_never_touch_imds(minio_endpoint: str) -> None:
    from app.ingestion.connectors.minio_connector import MinIOConnector

    config = _config(minio_endpoint, "private", **_CREDS)
    connector = MinIOConnector()
    health = await connector.validate_connection(config)
    assert health.ok, health.error
    docs = [d async for d, _c in connector.get_delta(config, None)]
    assert [d.content for d in docs] == [b"private tenant data"]
    assert connector.completed_cursor
    replayed = [
        d async for d in connector.replay_event(
            config, {"kind": "s3_object", "bucket": "private", "key": "docs/a.txt"}
        )
    ]
    assert [d.content for d in replayed] == [b"private tenant data"]
    assert await connector.estimate_doc_count_async(config) == 1


async def test_anonymous_sync_is_unsigned_and_never_touches_imds(minio_endpoint: str) -> None:
    from app.ingestion.connectors.minio_connector import MinIOConnector

    connector = MinIOConnector()
    docs = [d async for d, _c in connector.get_delta(_config(minio_endpoint, "public"), None)]
    assert [d.content for d in docs] == [b"public data"]
    # The same anonymous client on a private bucket: denied by MinIO, not ambient-signed.
    from app.ingestion.base_connector import ConnectorFetchError

    with pytest.raises(ConnectorFetchError, match="AccessDenied"):
        [d async for d in connector.get_delta(_config(minio_endpoint, "private"), None)]


async def test_undecryptable_keys_are_refused_before_any_request(minio_endpoint: str) -> None:
    from app.ingestion.base_connector import ConnectorSecretsUndecryptableError
    from app.ingestion.connectors.minio_connector import MinIOConnector

    config = _config(minio_endpoint, "private", credentials="")
    config.undecryptable_secrets = ["credentials"]
    with pytest.raises(ConnectorSecretsUndecryptableError, match="VAULT_MASTER_KEY"):
        [d async for d in MinIOConnector().get_delta(config, None)]
