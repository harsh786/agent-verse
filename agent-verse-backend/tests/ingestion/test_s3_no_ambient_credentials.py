"""BUG A: a tenant S3 / Kinesis client never uses botocore's ambient credential chain.

On the owner's cluster the worker's S3 sync failed with ``SSRF guard [s3]:
metadata service hostname '169.254.169.254' blocked``. The worker could not decrypt
the Source's stored credentials (its vault key differed from the API's), the store
blanked them, the connector built ``boto3.Session(aws_access_key_id=None, ...)``
and botocore resolved credentials through its DEFAULT chain — env vars, ~/.aws,
then the EC2 instance metadata service. Without the SSRF guard that would have
signed the tenant's requests with the platform pod's IAM role.

These tests drive real botocore clients (requests are answered at botocore's
``before-send`` hook, after signing, so nothing leaves the process) with every
ambient source booby-trapped: AWS_* env credentials are set to a "platform" key,
and the credential chain, any credential resolution and the IMDS fetcher raise if
they are ever consulted. They cover the listing client, the GetObject download,
the one-off client (webhook / DLQ replay, count estimate) and Kinesis — with the
tenant's keys (signed with exactly those) and without keys (explicitly UNSIGNED).
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from typing import Any

import pytest

from app.ingestion.base_connector import ConnectorSecretsUndecryptableError
from app.ingestion.connectors.kinesis_connector import KinesisConnector
from app.ingestion.connectors.minio_connector import MinIOConnector
from app.ingestion.connectors.s3_connector import S3Connector
from app.ingestion.source_config import CONNECTOR_FAILURE_KEY, SourceConfig, SourceFamily
from app.net import aws_clients

TENANT_KEY = "AKIATENANTEXAMPLE"
TENANT_SECRET = "tenant-secret-value"
AMBIENT_KEY = "AKIAPLATFORMAMBIENT"


class _Raw(io.BytesIO):
    """A urllib3-like raw body: ``read(n)`` for streaming bodies, ``stream()`` otherwise."""

    def stream(self, **_kw: Any) -> Iterator[bytes]:
        while chunk := self.read(4096):
            yield chunk


def _response(url: str, status: int, headers: dict[str, str], body: bytes) -> Any:
    from botocore.awsrequest import AWSResponse

    return AWSResponse(url, status, {"Content-Length": str(len(body)), **headers}, _Raw(body))


class _Endpoint:
    """Answers S3 / Kinesis requests in-process and records how each was signed."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {"docs/a.txt": b"hello tenant"}
        self.requests: list[tuple[str, str, str | None]] = []  # (operation, url, Authorization)

    def __call__(self, request: Any, event_name: str, **_kw: Any) -> Any:
        operation = event_name.rsplit(".", 1)[-1]
        auth = request.headers.get("Authorization")
        self.requests.append((operation, request.url, auth.decode() if isinstance(auth, bytes)
                              else auth))
        date = {"Date": "Wed, 07 Oct 2026 12:00:00 GMT"}
        if operation == "ListObjectsV2":
            contents = "".join(
                f"<Contents><Key>{k}</Key><LastModified>2026-10-01T00:00:00.000Z</LastModified>"
                f"<ETag>&quot;e&quot;</ETag><Size>{len(v)}</Size>"
                "<StorageClass>STANDARD</StorageClass></Contents>"
                for k, v in sorted(self.objects.items())
            )
            body = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                f"<Name>b</Name><Prefix></Prefix><KeyCount>{len(self.objects)}</KeyCount>"
                f"<MaxKeys>1000</MaxKeys><IsTruncated>false</IsTruncated>{contents}"
                "</ListBucketResult>"
            ).encode()
            return _response(request.url, 200, {"Content-Type": "application/xml", **date}, body)
        if operation == "GetObject":
            key = request.url.split("/b/", 1)[-1] if "/b/" in request.url else (
                request.url.split(".amazonaws.com/", 1)[-1]
            )
            return _response(request.url, 200, {"Content-Type": "text/plain", **date},
                             self.objects[key.split("?")[0]])
        if operation == "DescribeStreamSummary":
            body = b'{"StreamDescriptionSummary": {"OpenShardCount": 2, "StreamName": "s"}}'
            return _response(request.url, 200,
                             {"Content-Type": "application/x-amz-json-1.1"}, body)
        raise AssertionError(f"unexpected operation {operation}")


def _boom(what: str) -> Any:
    def _raise(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError(f"{what} was consulted for a tenant client")

    return _raise


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> _Endpoint:
    """Real botocore, answered in-process; every ambient credential source trapped."""
    import botocore.credentials
    import botocore.session
    import botocore.utils

    # The platform pod's ambient identity: must never sign a tenant request.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", AMBIENT_KEY)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "platform-ambient-secret")
    monkeypatch.setenv("AWS_PROFILE", "platform-profile-that-does-not-exist")
    monkeypatch.setenv("AWS_DEFAULTS_MODE", "auto")  # 'auto' would ask IMDS for the region
    # IMDS, the default chain, and any credential resolution at all.
    monkeypatch.setattr(botocore.utils.InstanceMetadataFetcher, "_fetch_metadata_token",
                        _boom("IMDS (token)"))
    monkeypatch.setattr(botocore.utils.InstanceMetadataFetcher, "_get_request",
                        _boom("IMDS (request)"))
    monkeypatch.setattr(botocore.utils.IMDSRegionProvider, "provide", _boom("IMDS region"))
    monkeypatch.setattr(botocore.credentials.InstanceMetadataProvider, "load",
                        _boom("the instance-metadata credential provider"))
    monkeypatch.setattr(botocore.credentials, "create_credential_resolver",
                        _boom("botocore's default credential chain"))
    monkeypatch.setattr(botocore.credentials.CredentialResolver, "load_credentials",
                        _boom("a credential resolver"))
    monkeypatch.setattr(botocore.session.Session, "get_credentials",
                        _boom("the session's credential lookup"))

    stub = _Endpoint()
    real_session = aws_clients.isolated_botocore_session

    def _session() -> Any:
        session = real_session()
        session.register("before-send", stub)
        return session

    monkeypatch.setattr(aws_clients, "isolated_botocore_session", _session)
    return stub


def _config(*, source_type: str = "s3", **cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-1", tenant_id="t1", name="s", family=SourceFamily.OBJECT_STORAGE,
        source_type=source_type, collection_id="c",
        connection_config={"bucket": "b", **cc},
    )


_WITH_KEYS = {"credentials": {"access_key_id": TENANT_KEY, "secret_access_key": TENANT_SECRET}}


def _assert_signed_by_tenant(endpoint: _Endpoint) -> None:
    assert endpoint.requests
    for operation, _url, auth in endpoint.requests:
        assert auth is not None, f"{operation} was not signed"
        assert f"Credential={TENANT_KEY}/" in auth, (operation, auth)
        assert AMBIENT_KEY not in auth


def _assert_unsigned(endpoint: _Endpoint) -> None:
    assert endpoint.requests
    for operation, _url, auth in endpoint.requests:
        assert auth is None, f"{operation} was signed: {auth}"


# ── listing + GetObject (the sync) ───────────────────────────────────────────


@pytest.mark.parametrize("cc", [_WITH_KEYS, {}], ids=["tenant-keys", "anonymous"])
async def test_sync_lists_and_downloads_without_the_ambient_chain(
    endpoint: _Endpoint, cc: dict[str, Any]
) -> None:
    docs = [d async for d, _c in S3Connector().get_delta(_config(**cc), None)]
    assert [d.content for d in docs] == [b"hello tenant"]
    assert {op for op, _u, _a in endpoint.requests} == {"ListObjectsV2", "GetObject"}
    if cc:
        _assert_signed_by_tenant(endpoint)
        # An explicit region even though the source names none.
        assert all("/us-east-1/s3/" in str(a) for _o, _u, a in endpoint.requests)
    else:
        _assert_unsigned(endpoint)


async def test_flat_credentials_and_a_session_token_are_the_tenants(endpoint: _Endpoint) -> None:
    cfg = _config(access_key_id=TENANT_KEY, secret_access_key=TENANT_SECRET, region="eu-west-1")
    docs = [d async for d, _c in S3Connector().get_delta(cfg, None)]
    assert len(docs) == 1
    _assert_signed_by_tenant(endpoint)
    assert all("/eu-west-1/s3/" in a for _o, _u, a in endpoint.requests if a)


async def test_validate_connection_uses_only_the_tenant_identity(endpoint: _Endpoint) -> None:
    health = await S3Connector().validate_connection(_config(**_WITH_KEYS))
    assert health.ok, health.error
    _assert_signed_by_tenant(endpoint)


# ── one-off client: webhook / DLQ replay fetch, count estimate ───────────────


@pytest.mark.parametrize("cc", [_WITH_KEYS, {}], ids=["tenant-keys", "anonymous"])
async def test_single_object_fetch_without_the_ambient_chain(
    endpoint: _Endpoint, cc: dict[str, Any]
) -> None:
    reference = {"kind": "s3_object", "bucket": "b", "key": "docs/a.txt"}
    docs = [d async for d in S3Connector().replay_event(_config(**cc), reference)]
    assert [d.content for d in docs] == [b"hello tenant"]
    assert CONNECTOR_FAILURE_KEY not in docs[0].metadata
    assert [op for op, _u, _a in endpoint.requests] == ["GetObject"]
    (_assert_signed_by_tenant if cc else _assert_unsigned)(endpoint)


@pytest.mark.parametrize("cc", [_WITH_KEYS, {}], ids=["tenant-keys", "anonymous"])
def test_count_estimate_without_the_ambient_chain(endpoint: _Endpoint, cc: dict[str, Any]) -> None:
    assert S3Connector().estimate_doc_count(_config(**cc)) == 1
    (_assert_signed_by_tenant if cc else _assert_unsigned)(endpoint)


def test_minio_client_keeps_path_style_and_an_explicit_region(endpoint: _Endpoint) -> None:
    import boto3
    from botocore import UNSIGNED

    cfg = _config(source_type="minio", endpoint_url="http://minio.example:9000", region="")
    client = MinIOConnector()._make_client(boto3, cfg, "http://minio.example:9000")
    assert client.meta.region_name == "us-east-1"
    assert client.meta.endpoint_url == "http://minio.example:9000"
    assert client.meta.config.s3 == {"addressing_style": "path"}
    assert client.meta.config.signature_version is UNSIGNED
    client.list_objects_v2(Bucket="b")
    assert endpoint.requests[0][1].startswith("http://minio.example:9000/b")
    _assert_unsigned(endpoint)


# ── refusals: never silently anonymous / ambient ─────────────────────────────


async def test_undecryptable_credentials_fail_loudly_with_the_reason(endpoint: _Endpoint) -> None:
    cfg = _config(credentials="")
    cfg.undecryptable_secrets = ["credentials"]
    with pytest.raises(ConnectorSecretsUndecryptableError, match="VAULT_MASTER_KEY"):
        [d async for d in S3Connector().get_delta(cfg, None)]
    with pytest.raises(ConnectorSecretsUndecryptableError):
        [d async for d in S3Connector().replay_event(
            cfg, {"kind": "s3_object", "bucket": "b", "key": "docs/a.txt"})]
    health = await S3Connector().validate_connection(cfg)
    assert not health.ok and "could not be decrypted" in str(health.error)
    assert endpoint.requests == []  # nothing was sent, signed or not


@pytest.mark.parametrize(
    ("cc", "reason"),
    [
        ({"credentials": {"access_key_id": TENANT_KEY}}, "secret_access_key is missing"),
        ({"credentials": {"secret_access_key": TENANT_SECRET}}, "access_key_id is missing"),
        ({"credentials": "********"}, "mask"),
        ({"credentials": {"access_key_id": TENANT_KEY, "secret_access_key": "********"}},
         "mask"),
    ],
)
async def test_unusable_credentials_are_refused_not_run_anonymous(
    endpoint: _Endpoint, cc: dict[str, Any], reason: str
) -> None:
    from app.ingestion.base_connector import ConnectorFetchError

    with pytest.raises(ConnectorFetchError, match=reason):
        [d async for d in S3Connector().get_delta(_config(**cc), None)]
    docs = [d async for d in S3Connector().replay_event(
        _config(**cc), {"kind": "s3_object", "bucket": "b", "key": "docs/a.txt"})]
    assert reason in docs[0].metadata[CONNECTOR_FAILURE_KEY]
    assert endpoint.requests == []


# ── Kinesis ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cc",
    [{"access_key_id": TENANT_KEY, "secret_access_key": TENANT_SECRET}, {}],
    ids=["tenant-keys", "anonymous"],
)
async def test_kinesis_never_uses_the_ambient_chain(
    endpoint: _Endpoint, cc: dict[str, Any]
) -> None:
    cfg = SourceConfig(source_id="k", tenant_id="t1", name="k", family=SourceFamily.STREAMING,
                       source_type="kinesis", connection_config={"stream_name": "s", **cc})
    health = await KinesisConnector().validate_connection(cfg)
    assert health.ok, health.error
    assert health.metadata["open_shards"] == 2
    (_assert_signed_by_tenant if cc else _assert_unsigned)(endpoint)


async def test_kinesis_refuses_undecryptable_credentials(endpoint: _Endpoint) -> None:
    cfg = SourceConfig(source_id="k", tenant_id="t1", name="k", family=SourceFamily.STREAMING,
                       source_type="kinesis", connection_config={"stream_name": "s"})
    cfg.undecryptable_secrets = ["secret_access_key"]
    health = await KinesisConnector().validate_connection(cfg)
    assert not health.ok and "could not be decrypted" in str(health.error)
    with pytest.raises(ConnectorSecretsUndecryptableError):
        [d async for d in KinesisConnector().get_delta(cfg, None)]
    assert endpoint.requests == []


# ── the isolated session itself ──────────────────────────────────────────────


def test_isolated_session_has_no_credential_providers_and_ignores_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", AMBIENT_KEY)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "platform-ambient-secret")
    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "http://redirected.example:1")
    monkeypatch.setenv("AWS_PROFILE", "platform-profile-that-does-not-exist")
    session = aws_clients.isolated_botocore_session()
    assert session.get_component("credential_provider").providers == []
    assert session.get_credentials() is None
    assert session.get_config_variable("defaults_mode") == "legacy"
    import boto3

    client = aws_clients.tenant_client(boto3, "s3", region=None, keys=None)
    assert client.meta.endpoint_url == "https://s3.amazonaws.com"  # env redirect ignored
    assert client.meta.region_name == aws_clients.DEFAULT_REGION
