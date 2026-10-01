"""BLOCKING-SDK: connector SDK calls must not run on the event loop.

clickhouse-connect, boto3, azure-storage-blob, snowflake, google-cloud-*,
googleapiclient, duckdb and confluent-kafka are synchronous. Called straight from
a coroutine, every network round-trip froze the whole worker loop — other syncs,
API requests, heartbeats. Each fake SDK here sleeps ``SLOW`` seconds per call
(what a real round-trip does to the caller's thread) while a probe task measures
how late the loop wakes it up. On the loop, the lag is ~SLOW; off it, ~0.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from types import FunctionType, ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion.source_config import SourceConfig, SourceFamily

# A blocking call stalls the loop for >= SLOW; the margin keeps the probe stable on a
# heavily loaded CI box (scheduler hiccups of ~0.1-0.15 s were seen at load ~27).
SLOW = 0.5
MAX_LAG = 0.3


def _slow(result: Any = None) -> Callable[..., Any]:
    """A blocking SDK call: sleeps, then returns ``result`` (a plain function is
    called for a fresh value, e.g. a new iterator per call)."""

    def _call(*_a: Any, **_k: Any) -> Any:
        time.sleep(SLOW)
        return result() if isinstance(result, FunctionType) else result

    return _call


@contextlib.asynccontextmanager
async def loop_lag_probe(interval: float = 0.01) -> AsyncIterator[list[float]]:
    """Record how late each ``interval`` tick of the event loop fires."""
    lags: list[float] = []
    stop = asyncio.Event()

    async def _tick() -> None:
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            start = loop.time()
            await asyncio.sleep(interval)
            lags.append(loop.time() - start - interval)

    task = asyncio.create_task(_tick())
    await asyncio.sleep(0)
    try:
        yield lags
    finally:
        stop.set()
        await task


async def _assert_off_loop(make: Callable[[], Any]) -> Any:
    async with loop_lag_probe() as lags:
        result = await make()
    assert lags, "probe never ran"
    assert max(lags) < MAX_LAG, f"event loop blocked for {max(lags):.3f}s"
    return result


def _config(source_type: str, **cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id=f"src-{source_type}",
        tenant_id="tenant-loop",
        name=source_type,
        family=SourceFamily.WEB,
        source_type=source_type,
        collection_id="col-1",
        connection_config=cc,
    )


async def _drain(gen: AsyncIterator[Any]) -> list[Any]:
    return [item async for item in gen]


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK endpoints resolve public without real DNS."""
    import app.net.ssrf_guard as sg

    monkeypatch.setattr(sg, "_resolve_host", lambda host: ["20.60.1.1"])


# ── clickhouse ────────────────────────────────────────────────────────────────


def _fake_clickhouse() -> dict[str, ModuleType]:
    result = MagicMock(column_names=["id", "updated_at"], result_rows=[(1, "2026-01-01")])
    result.first_row = ["24.8"]
    client = MagicMock()
    client.query.side_effect = _slow(result)
    mod = ModuleType("clickhouse_connect")
    mod.get_client = _slow(client)  # type: ignore[attr-defined]
    return {"clickhouse_connect": mod}


async def test_clickhouse_validate_is_off_loop() -> None:
    from app.ingestion.connectors.clickhouse_connector import ClickHouseConnector

    with patch.dict(sys.modules, _fake_clickhouse()):
        health = await _assert_off_loop(
            lambda: ClickHouseConnector().validate_connection(
                _config("clickhouse", host="ch.local")
            )
        )
    assert health.ok, health.error


async def test_clickhouse_get_delta_is_off_loop() -> None:
    from app.ingestion.connectors.clickhouse_connector import ClickHouseConnector

    cfg = _config("clickhouse", host="ch.local", table="t")
    with patch.dict(sys.modules, _fake_clickhouse()):
        docs = await _assert_off_loop(lambda: _drain(ClickHouseConnector().get_delta(cfg, None)))
    assert len(docs) == 1


# ── s3 / minio (boto3) ────────────────────────────────────────────────────────


def _fake_boto3() -> dict[str, ModuleType]:
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    body = MagicMock()
    body.read.side_effect = _slow(b"hello")
    s3 = MagicMock()
    s3.head_bucket.side_effect = _slow({})
    s3.list_objects_v2.side_effect = _slow({"KeyCount": 1})
    s3.get_object.side_effect = _slow({"Body": body, "ContentType": "text/plain"})

    def _pages(**_k: Any) -> Any:
        time.sleep(SLOW)
        yield {"Contents": [{"Key": "a.txt", "LastModified": stamp, "Size": 5}]}

    s3.get_paginator.return_value.paginate.side_effect = _pages
    session = MagicMock()
    session.client.side_effect = _slow(s3)
    mod = ModuleType("boto3")
    mod.Session = MagicMock(return_value=session)  # type: ignore[attr-defined]
    mod.client = _slow(s3)  # type: ignore[attr-defined]
    return {"boto3": mod}


async def test_s3_validate_is_off_loop() -> None:
    from app.ingestion.connectors.s3_connector import S3Connector

    with patch.dict(sys.modules, _fake_boto3()):
        health = await _assert_off_loop(
            lambda: S3Connector().validate_connection(_config("s3", bucket="b"))
        )
    assert health.ok, health.error


async def test_s3_get_delta_is_off_loop() -> None:
    from app.ingestion.connectors.s3_connector import S3Connector

    with patch.dict(sys.modules, _fake_boto3()):
        docs = await _assert_off_loop(
            lambda: _drain(S3Connector().get_delta(_config("s3", bucket="b"), None))
        )
    assert [d.content for d, _c in docs] == [b"hello"]


async def test_s3_webhook_fetch_is_off_loop() -> None:
    from app.ingestion.connectors.s3_connector import S3Connector

    with patch.dict(sys.modules, _fake_boto3()):
        docs = await _assert_off_loop(
            lambda: _drain(S3Connector()._fetch_single(_config("s3", bucket="b"), "b", "a.txt"))
        )
    assert len(docs) == 1


async def test_minio_get_delta_is_off_loop() -> None:
    from app.ingestion.connectors.minio_connector import MinIOConnector

    cfg = _config("minio", bucket="b", endpoint_url="http://db:9000")
    with patch.dict(sys.modules, _fake_boto3()):
        docs = await _assert_off_loop(lambda: _drain(MinIOConnector().get_delta(cfg, None)))
    assert len(docs) == 1


# ── azure blob ────────────────────────────────────────────────────────────────


def _fake_azure() -> dict[str, ModuleType]:
    blob = MagicMock(last_modified=datetime(2026, 1, 1, tzinfo=UTC), size=5)
    blob.name = "a.txt"
    blob.content_settings.content_type = "text/plain"
    downloader = MagicMock()
    downloader.chunks.side_effect = _slow(lambda: iter([b"hello"]))
    container = MagicMock()
    container.get_container_properties.side_effect = _slow({"lease": {"state": "available"}})
    container.list_blobs.side_effect = _slow(lambda: iter([blob]))
    container.download_blob.side_effect = _slow(downloader)
    service = MagicMock()
    service.get_container_client.return_value = container
    azure = ModuleType("azure")
    storage = ModuleType("azure.storage")
    blob_mod = ModuleType("azure.storage.blob")
    blob_mod.BlobServiceClient = MagicMock(return_value=service)  # type: ignore[attr-defined]
    storage.blob = blob_mod  # type: ignore[attr-defined]
    azure.storage = storage  # type: ignore[attr-defined]
    return {"azure": azure, "azure.storage": storage, "azure.storage.blob": blob_mod}


_AZ = {"account_name": "acct123", "account_key": "k", "container": "c"}


async def test_azure_validate_is_off_loop() -> None:
    from app.ingestion.connectors.azure_blob_connector import AzureBlobConnector

    with patch.dict(sys.modules, _fake_azure()):
        health = await _assert_off_loop(
            lambda: AzureBlobConnector().validate_connection(_config("azure_blob", **_AZ))
        )
    assert health.ok, health.error


async def test_azure_get_delta_is_off_loop() -> None:
    from app.ingestion.connectors.azure_blob_connector import AzureBlobConnector

    with patch.dict(sys.modules, _fake_azure()):
        docs = await _assert_off_loop(
            lambda: _drain(AzureBlobConnector().get_delta(_config("azure_blob", **_AZ), None))
        )
    assert [d.content for d, _c in docs] == [b"hello"]


# ── snowflake ─────────────────────────────────────────────────────────────────


def _fake_snowflake() -> dict[str, ModuleType]:
    cur = MagicMock()
    cur.execute.side_effect = _slow(None)
    cur.fetchone.side_effect = _slow(("8.0",))
    batches = iter([[{"ID": 1, "UPDATED_AT": "2026"}], []])
    cur.fetchmany.side_effect = lambda *_a, **_k: (time.sleep(SLOW), next(batches))[1]
    cur.__iter__ = lambda self: (time.sleep(SLOW), iter([{"ID": 1, "UPDATED_AT": "2026"}]))[1]
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.close.side_effect = _slow(None)
    connector = ModuleType("snowflake.connector")
    connector.connect = _slow(conn)  # type: ignore[attr-defined]
    connector.DictCursor = object  # type: ignore[attr-defined]
    pkg = ModuleType("snowflake")
    pkg.connector = connector  # type: ignore[attr-defined]
    return {"snowflake": pkg, "snowflake.connector": connector}


async def test_snowflake_validate_is_off_loop() -> None:
    from app.ingestion.connectors.snowflake_connector import SnowflakeConnector

    with patch.dict(sys.modules, _fake_snowflake()):
        health = await _assert_off_loop(
            lambda: SnowflakeConnector().validate_connection(_config("snowflake", account="a"))
        )
    assert health.ok, health.error


async def test_snowflake_get_delta_is_off_loop() -> None:
    from app.ingestion.connectors.snowflake_connector import SnowflakeConnector

    cfg = _config("snowflake", account="a", query="SELECT 1")
    with patch.dict(sys.modules, _fake_snowflake()):
        docs = await _assert_off_loop(lambda: _drain(SnowflakeConnector().get_delta(cfg, None)))
    assert len(docs) == 1


# ── google cloud: bigquery / gcs / pubsub ────────────────────────────────────


def _fake_bigquery() -> dict[str, ModuleType]:
    def _rows() -> Any:
        time.sleep(SLOW)
        yield {"id": 1, "updated_at": "2026"}

    job = MagicMock()
    job.result.side_effect = _slow(_rows)
    client = MagicMock()
    client.query.side_effect = _slow(job)
    client.list_datasets.side_effect = _slow(lambda: iter([]))
    mod = ModuleType("google.cloud.bigquery")
    mod.Client = MagicMock(side_effect=_slow(client))  # type: ignore[attr-defined]
    return {"google.cloud.bigquery": mod}


async def test_bigquery_validate_is_off_loop() -> None:
    from app.ingestion.connectors.bigquery_connector import BigQueryConnector

    with patch.dict(sys.modules, _fake_bigquery()):
        health = await _assert_off_loop(
            lambda: BigQueryConnector().validate_connection(_config("bigquery", project="p"))
        )
    assert health.ok, health.error


async def test_bigquery_get_delta_is_off_loop() -> None:
    from app.ingestion.connectors.bigquery_connector import BigQueryConnector

    with patch.dict(sys.modules, _fake_bigquery()):
        docs = await _assert_off_loop(
            lambda: _drain(BigQueryConnector().get_delta(_config("bigquery", project="p"), None))
        )
    assert len(docs) == 1


def _fake_gcs() -> dict[str, ModuleType]:
    blob = MagicMock(updated=datetime(2026, 1, 1, tzinfo=UTC), size=5, content_type="text/plain")
    blob.name = "a.txt"
    blob.download_as_bytes.side_effect = _slow(b"hello")

    def _blobs(*_a: Any, **_k: Any) -> Any:
        time.sleep(SLOW)
        yield blob

    client = MagicMock()
    client.list_blobs.side_effect = _blobs
    client.bucket.return_value.exists.side_effect = _slow(True)
    mod = ModuleType("google.cloud.storage")
    mod.Client = MagicMock(side_effect=_slow(client))  # type: ignore[attr-defined]
    return {"google.cloud.storage": mod}


async def test_gcs_validate_is_off_loop() -> None:
    from app.ingestion.connectors.gcs_connector import GCSConnector

    with patch.dict(sys.modules, _fake_gcs()):
        health = await _assert_off_loop(
            lambda: GCSConnector().validate_connection(_config("gcs", bucket="b"))
        )
    assert health.ok, health.error


async def test_gcs_get_delta_is_off_loop() -> None:
    from app.ingestion.connectors.gcs_connector import GCSConnector

    with patch.dict(sys.modules, _fake_gcs()):
        docs = await _assert_off_loop(
            lambda: _drain(GCSConnector().get_delta(_config("gcs", bucket="b"), None))
        )
    assert [d.content for d, _c in docs] == [b"hello"]


def _fake_pubsub() -> dict[str, ModuleType]:
    subscriber = MagicMock()
    subscriber.get_subscription.side_effect = _slow({})
    mod = ModuleType("google.cloud.pubsub_v1")
    mod.SubscriberClient = MagicMock(side_effect=_slow(subscriber))  # type: ignore[attr-defined]
    return {"google.cloud.pubsub_v1": mod}


async def test_pubsub_validate_is_off_loop() -> None:
    from app.ingestion.connectors.pubsub_connector import PubSubConnector

    with patch.dict(sys.modules, _fake_pubsub()):
        health = await _assert_off_loop(
            lambda: PubSubConnector().validate_connection(
                _config("pubsub", project="p", subscription="s")
            )
        )
    assert health.ok, health.error


# ── kinesis (boto3) ───────────────────────────────────────────────────────────


def _fake_kinesis_boto3() -> dict[str, ModuleType]:
    kinesis = MagicMock()
    kinesis.describe_stream_summary.side_effect = _slow(
        {"StreamDescriptionSummary": {"OpenShardCount": 1}}
    )
    kinesis.list_shards.side_effect = _slow({"Shards": [{"ShardId": "s-1"}]})
    kinesis.get_shard_iterator.side_effect = _slow({"ShardIterator": "it"})
    kinesis.get_records.side_effect = _slow(
        {"Records": [{"SequenceNumber": "1", "Data": b'{"a": 1}'}]}
    )
    session = MagicMock()
    session.client.side_effect = _slow(kinesis)
    mod = ModuleType("boto3")
    mod.Session = MagicMock(return_value=session)  # type: ignore[attr-defined]
    return {"boto3": mod}


async def test_kinesis_validate_is_off_loop() -> None:
    from app.ingestion.connectors.kinesis_connector import KinesisConnector

    with patch.dict(sys.modules, _fake_kinesis_boto3()):
        health = await _assert_off_loop(
            lambda: KinesisConnector().validate_connection(_config("kinesis", stream_name="s"))
        )
    assert health.ok, health.error


async def test_kinesis_get_delta_is_off_loop() -> None:
    from app.ingestion.connectors.kinesis_connector import KinesisConnector

    with patch.dict(sys.modules, _fake_kinesis_boto3()):
        docs = await _assert_off_loop(
            lambda: _drain(KinesisConnector().get_delta(_config("kinesis", stream_name="s"), None))
        )
    assert len(docs) == 1


# ── google drive ──────────────────────────────────────────────────────────────


def _fake_drive_client() -> Any:
    client = MagicMock()
    client.list_files.side_effect = _slow(
        [{"id": "f1", "name": "a", "mimeType": "text/plain", "modifiedTime": "2026"}]
    )
    client.download_file.side_effect = _slow("hello")
    return client


async def test_gdrive_validate_and_get_delta_are_off_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ingestion.connectors.gdrive_connector import GDriveSourceConnector

    monkeypatch.setattr(GDriveSourceConnector, "_client", lambda self, cfg: _fake_drive_client())
    cfg = _config("gdrive", folder_id="x")
    health = await _assert_off_loop(lambda: GDriveSourceConnector().validate_connection(cfg))
    assert health.ok, health.error
    docs = await _assert_off_loop(lambda: _drain(GDriveSourceConnector().get_delta(cfg, None)))
    assert len(docs) == 1


# ── duckdb ────────────────────────────────────────────────────────────────────


def _fake_duckdb() -> dict[str, ModuleType]:
    result = MagicMock(description=[("id",), ("updated_at",)])
    result.fetchall.side_effect = _slow([(1, "2026")])
    con = MagicMock()
    con.execute.side_effect = _slow(result)
    mod = ModuleType("duckdb")
    mod.connect = _slow(con)  # type: ignore[attr-defined]
    return {"duckdb": mod}


async def test_duckdb_validate_and_get_delta_are_off_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from app.ingestion.connectors import duckdb_connector
    from app.ingestion.connectors.duckdb_connector import DuckDBConnector

    monkeypatch.setattr(duckdb_connector, "_tenant_root", lambda tenant_id: tmp_path)
    cfg = _config("duckdb", database=":memory:", query="SELECT 1")
    with patch.dict(sys.modules, _fake_duckdb()):
        health = await _assert_off_loop(lambda: DuckDBConnector().validate_connection(cfg))
        assert health.ok, health.error
        docs = await _assert_off_loop(lambda: _drain(DuckDBConnector().get_delta(cfg, None)))
    assert len(docs) == 1


# ── kafka (validate, with strict pinning opted out) ──────────────────────────


async def test_kafka_validate_is_off_loop(allow_unpinnable_drivers: None) -> None:
    from app.ingestion.connectors import kafka_connector
    from app.ingestion.connectors.kafka_connector import KafkaConnector

    metadata = MagicMock(brokers={}, topics={})
    admin = MagicMock()
    admin.list_topics.side_effect = _slow(metadata)
    pkg = ModuleType("confluent_kafka")
    admin_mod = ModuleType("confluent_kafka.admin")
    admin_mod.AdminClient = MagicMock(return_value=admin)  # type: ignore[attr-defined]
    pkg.admin = admin_mod  # type: ignore[attr-defined]
    with (
        patch.dict(sys.modules, {"confluent_kafka": pkg, "confluent_kafka.admin": admin_mod}),
        patch.object(kafka_connector, "_require_bootstrap", lambda cc: "db:9092"),
    ):
        health = await _assert_off_loop(
            lambda: KafkaConnector().validate_connection(_config("kafka", bootstrap_servers="db"))
        )
    assert health.ok, health.error


# ── mqtt (MQTT-BLOCK) ─────────────────────────────────────────────────────────


class _SlowMqttClient:
    """paho's loop_stop() joins the network thread and disconnect() writes to the
    socket — both block the caller (here: SLOW seconds each)."""

    def __init__(self, *_a: Any, **_k: Any) -> None:
        self.on_connect: Any = None
        self.on_message: Any = None

    def username_pw_set(self, *_a: Any) -> None: ...

    def connect(self, *_a: Any) -> None:
        time.sleep(SLOW)

    def connect_async(self, *_a: Any) -> None: ...

    def subscribe(self, *_a: Any, **_k: Any) -> None: ...

    def loop_start(self) -> None:
        if self.on_connect is not None:
            self.on_connect(self, None, {}, SimpleNamespace(is_failure=False), None)
        if self.on_message is not None:
            self.on_message(self, None, MagicMock(payload=b'{"t": 1}', topic="a/b", qos=0))

    def loop_stop(self) -> None:
        time.sleep(SLOW)

    def disconnect(self) -> None:
        time.sleep(SLOW)


def _fake_paho() -> dict[str, ModuleType]:
    paho = ModuleType("paho")
    mqtt = ModuleType("paho.mqtt")
    client_mod = ModuleType("paho.mqtt.client")
    client_mod.Client = _SlowMqttClient  # type: ignore[attr-defined]
    client_mod.CallbackAPIVersion = SimpleNamespace(VERSION2="VERSION2")  # type: ignore[attr-defined]
    mqtt.client = client_mod  # type: ignore[attr-defined]
    paho.mqtt = mqtt  # type: ignore[attr-defined]
    return {"paho": paho, "paho.mqtt": mqtt, "paho.mqtt.client": client_mod}


async def test_mqtt_validate_is_off_loop() -> None:
    from app.ingestion.connectors.mqtt_connector import MQTTConnector

    with patch.dict(sys.modules, _fake_paho()):
        health = await _assert_off_loop(
            lambda: MQTTConnector().validate_connection(_config("mqtt", host="db", port=1883))
        )
    assert health.ok, health.error


async def test_mqtt_get_delta_is_off_loop() -> None:
    from app.ingestion.connectors.mqtt_connector import MQTTConnector

    cfg = _config("mqtt", host="db", port=1883, topics=["a/#"], timeout_seconds=0.05)
    with patch.dict(sys.modules, _fake_paho()):
        docs = await _assert_off_loop(lambda: _drain(MQTTConnector().get_delta(cfg, None)))
    assert len(docs) == 1
