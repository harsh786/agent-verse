"""STABLE-DOC-IDS: every connector derives document ids from the Source + item identity.

Each connector used to mint ``uuid4()`` per sync, so a re-sync of an edited item
indexed it again beside its stale version (only identical content was caught by
the content-hash dedup). For every affected connector, the same upstream data
synced twice must give the same ids, distinct items distinct ids, and a second
Source reading the same upstream must get different ids (no cross-Source
collisions, which the pipeline's replace-on-same-id would otherwise turn into one
Source overwriting another's documents).

Fakes are the existing per-connector test doubles where they exist.
"""

from __future__ import annotations

from types import SimpleNamespace

import copy
import sys
from collections.abc import Awaitable, Callable, Iterator
from contextlib import ExitStack, contextmanager
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily

Run = Callable[[SourceConfig], Awaitable[list[RawDocument]]]


def _cfg(source_type: str, cc: dict[str, Any], source_id: str) -> SourceConfig:
    return SourceConfig(
        source_id=source_id,
        tenant_id="tenant-ids",
        name=source_type,
        family=SourceFamily.WEB,
        source_type=source_type,
        collection_id="col-1",
        connection_config=copy.deepcopy(cc),
    )


async def _drain(gen: Any) -> list[RawDocument]:
    return [doc async for doc, _cursor in gen]


async def _assert_stable(
    run: Run, source_type: str, cc: dict[str, Any], *, min_docs: int = 2
) -> list[str]:
    first = [d.doc_id for d in await run(_cfg(source_type, cc, "src-a"))]
    second = [d.doc_id for d in await run(_cfg(source_type, cc, "src-a"))]
    other = [d.doc_id for d in await run(_cfg(source_type, cc, "src-b"))]
    assert len(first) >= min_docs, first
    assert first == second, "ids changed between two syncs of the same upstream data"
    assert len(set(first)) == len(first), "distinct items share an id"
    assert set(first).isdisjoint(other), "two Sources produced the same document id"
    return first


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK endpoints resolve public without real DNS."""
    import app.net.ssrf_guard as sg

    monkeypatch.setattr(sg, "_resolve_host", lambda host: ["20.60.1.1"])


# ── HTTP connectors ────────────────────────────────────────────────────────────


class _Resp:
    def __init__(
        self,
        payload: Any = None,
        *,
        text: str = "",
        status: int = 200,
        links: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.text = text
        self.content = text.encode()
        self.status_code = status
        self.is_success = status < 400
        self.links = links or {}
        self.headers = headers or {}

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if not self.is_success:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    def __init__(self, router: Callable[[str, str, dict[str, Any]], _Resp]) -> None:
        self._router = router

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, url: str, **kw: Any) -> _Resp:
        return self._router("GET", str(url), kw)

    async def post(self, url: str, **kw: Any) -> _Resp:
        return self._router("POST", str(url), kw)

    async def request(self, method: str, url: str, **kw: Any) -> _Resp:
        return self._router(method, str(url), kw)


@contextmanager
def _http(module_name: str, router: Callable[[str, str, dict[str, Any]], _Resp]) -> Iterator[None]:
    import importlib

    module = importlib.import_module(f"app.ingestion.connectors.{module_name}_connector")
    with ExitStack() as stack:
        stack.enter_context(patch("httpx.AsyncClient", lambda *a, **k: _Client(router)))
        if hasattr(module, "source_client"):
            stack.enter_context(
                patch.object(module, "source_client", lambda *a, **k: _Client(router))
            )
        yield


def _http_run(module_name: str, cls_name: str, router: Any) -> Run:
    async def run(config: SourceConfig) -> list[RawDocument]:
        import importlib

        module = importlib.import_module(f"app.ingestion.connectors.{module_name}_connector")
        with _http(module_name, router):
            return await _drain(getattr(module, cls_name)().get_delta(config, None))

    return run


_ARXIV_XML = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry><id>http://arxiv.org/abs/2401.00001v1</id><published>2026-01-01T00:00:00Z</published>
    <title>A</title><summary>a</summary></entry>
  <entry><id>http://arxiv.org/abs/2401.00002v1</id><published>2026-01-02T00:00:00Z</published>
    <title>B</title><summary>b</summary></entry>
</feed>"""


async def test_arxiv() -> None:
    await _assert_stable(
        _http_run("arxiv", "ArXivConnector", lambda m, u, k: _Resp(text=_ARXIV_XML)),
        "arxiv",
        {"categories": ["cs.AI"]},
    )


async def test_confluence() -> None:
    page = lambda i: {  # noqa: E731
        "id": i, "title": f"P{i}", "version": {"when": f"2026-01-0{i}"},
        "body": {"view": {"value": f"<p>body {i}</p>"}},
    }
    router = lambda m, u, k: _Resp({"results": [page("1"), page("2")], "_links": {}})  # noqa: E731
    await _assert_stable(
        _http_run("confluence", "ConfluenceConnector", router),
        "confluence",
        {"base_url": "https://acme.atlassian.net", "space_keys": ["ENG"], "content_types": ["page"]},
    )


async def test_discord() -> None:
    msgs = [{"id": "11", "content": "b", "author": {}}, {"id": "10", "content": "a", "author": {}}]
    await _assert_stable(
        _http_run("discord", "DiscordConnector", lambda m, u, k: _Resp(msgs)),
        "discord",
        {"bot_token": "t", "channel_ids": ["c1"]},
    )


async def test_elasticsearch() -> None:
    hits = {"hits": {"hits": [
        {"_index": "idx", "_id": "a", "_source": {"@timestamp": 1}, "sort": [1, 7]},
        {"_index": "idx", "_id": "b", "_source": {"@timestamp": 2}, "sort": [2, 8]},
    ]}}

    def router(method: str, url: str, kw: dict[str, Any]) -> _Resp:
        if "/_mapping/field/" in url:
            return _Resp({"idx": {"mappings": {"@timestamp": {"mapping": {
                "@timestamp": {"type": "date"}}}}}})
        if url.endswith("/_pit") and method == "POST":
            return _Resp({"id": "pit-1"})
        if method == "DELETE":
            return _Resp({})
        return _Resp(hits)

    await _assert_stable(
        _http_run("elasticsearch", "ElasticsearchConnector", router),
        "elasticsearch",
        {"url": "http://es:9200", "index": "idx"},
    )


async def test_gitlab() -> None:
    def router(m: str, u: str, k: dict[str, Any]) -> _Resp:
        item = {"iid": 7, "title": "t", "updated_at": "2026-01-01", "web_url": u}
        return _Resp([item])

    ids = await _assert_stable(
        _http_run("gitlab", "GitLabConnector", router),
        "gitlab",
        {"project_ids": [5], "ingest_types": ["issues", "merge_requests"], "token": "t"},
    )
    assert len(ids) == 2  # issue #7 and MR !7 do not collide


async def test_jira() -> None:
    issues = {"issues": [{"key": "P-1", "fields": {}}, {"key": "P-2", "fields": {}}], "total": 2}
    await _assert_stable(
        _http_run("jira", "JiraConnector", lambda m, u, k: _Resp(issues)),
        "jira",
        {"base_url": "https://acme.atlassian.net", "project_keys": ["P"]},
    )


async def test_hubspot() -> None:
    data = {"results": [{"id": "1", "properties": {"a": "x"}}, {"id": "2", "properties": {}}]}
    await _assert_stable(
        _http_run("hubspot", "HubSpotConnector", lambda m, u, k: _Resp(data)),
        "hubspot",
        {"access_token": "t", "object_types": ["contacts"]},
    )


async def test_pagerduty() -> None:
    data = {"incidents": [{"id": "PI1"}, {"id": "PI2"}], "more": False}
    await _assert_stable(
        _http_run("pagerduty", "PagerDutyConnector", lambda m, u, k: _Resp(data)),
        "pagerduty",
        {"api_token": "t"},
    )


async def test_salesforce() -> None:
    from app.ingestion.connectors.salesforce_connector import SalesforceConnector

    data = {"records": [{"Id": "500A", "Subject": "a"}, {"Id": "500B", "Subject": "b"}]}
    with patch.object(
        SalesforceConnector,
        "_authenticate",
        AsyncMock(return_value=("tok", "https://example.com")),
    ):
        await _assert_stable(
            _http_run("salesforce", "SalesforceConnector", lambda m, u, k: _Resp(data)),
            "salesforce",
            {"sobjects": ["Case"], "fields": {"Case": ["Id", "Subject"]}},
        )


async def test_sentry() -> None:
    issues = [{"id": "1", "lastSeen": "2026"}, {"id": "2", "lastSeen": "2026"}]
    await _assert_stable(
        _http_run("sentry", "SentryConnector", lambda m, u, k: _Resp(issues)),
        "sentry",
        {"auth_token": "t", "org_slug": "o", "project_slugs": ["p"], "base_url": "https://example.com"},
    )


async def test_servicenow() -> None:
    data = {"result": [{"sys_id": "a", "number": "INC1"}, {"sys_id": "b", "number": "INC2"}]}
    await _assert_stable(
        _http_run("servicenow", "ServiceNowConnector", lambda m, u, k: _Resp(data)),
        "servicenow",
        {"instance": "dev", "tables": ["incident"]},
    )


async def test_teams() -> None:
    from app.ingestion.connectors.teams_connector import TeamsConnector

    data = {"value": [
        {"id": "m1", "body": {"content": "hello"}},
        {"id": "m2", "body": {"content": "world"}},
    ]}
    with patch.object(TeamsConnector, "_get_token", AsyncMock(return_value="tok")):
        await _assert_stable(
            _http_run("teams", "TeamsConnector", lambda m, u, k: _Resp(data)),
            "teams",
            {"team_id": "T", "channel_ids": ["C"]},
        )


async def test_zendesk() -> None:
    def router(m: str, u: str, k: dict[str, Any]) -> _Resp:
        if "articles" in u:
            return _Resp({"articles": [{"id": 1, "title": "a", "body": "x", "updated_at": "2026"}]})
        return _Resp({"tickets": [{"id": 1}, {"id": 2}], "end_of_stream": True})

    ids = await _assert_stable(
        _http_run("zendesk", "ZendeskConnector", router),
        "zendesk",
        {"subdomain": "acme", "ingest_types": ["tickets", "articles"]},
    )
    assert len(ids) == 3  # ticket 1 and article 1 do not collide


async def test_youtube() -> None:
    from app.ingestion.connectors.youtube_connector import YouTubeConnector

    fake = ModuleType("youtube_transcript_api")

    class _DisabledError(Exception):
        pass

    # youtube-transcript-api >= 1.0: YouTubeTranscriptApi(...).fetch(...).to_raw_data()
    api = MagicMock()
    api.return_value.fetch.return_value.to_raw_data.return_value = [{"text": "hello there"}]
    fake.YouTubeTranscriptApi = api  # type: ignore[attr-defined]
    fake.TranscriptsDisabled = _DisabledError  # type: ignore[attr-defined]

    async def run(config: SourceConfig) -> list[RawDocument]:
        with patch.dict(sys.modules, {"youtube_transcript_api": fake}):
            return await _drain(YouTubeConnector().get_delta(config, None))

    await _assert_stable(run, "youtube", {"video_ids": ["v1", "v2"]})


# ── SDK / driver connectors ────────────────────────────────────────────────────


async def test_azure_blob() -> None:
    from app.ingestion.connectors.azure_blob_connector import AzureBlobConnector
    from tests.ingestion.test_sdk_calls_off_loop import _AZ, _fake_azure

    async def run(config: SourceConfig) -> list[RawDocument]:
        with patch.dict(sys.modules, _fake_azure()):
            return await _drain(AzureBlobConnector().get_delta(config, None))

    await _assert_stable(run, "azure_blob", _AZ, min_docs=1)


async def test_clickhouse() -> None:
    from app.ingestion.connectors.clickhouse_connector import ClickHouseConnector
    from tests.ingestion.test_sdk_calls_off_loop import _fake_clickhouse

    async def run(config: SourceConfig) -> list[RawDocument]:
        with patch.dict(sys.modules, _fake_clickhouse()):
            return await _drain(ClickHouseConnector().get_delta(config, None))

    await _assert_stable(run, "clickhouse", {"host": "ch.local", "table": "t"}, min_docs=1)


async def test_bigquery() -> None:
    from app.ingestion.connectors.bigquery_connector import BigQueryConnector
    from tests.ingestion.test_sdk_calls_off_loop import _fake_bigquery

    async def run(config: SourceConfig) -> list[RawDocument]:
        with patch.dict(sys.modules, _fake_bigquery()):
            return await _drain(BigQueryConnector().get_delta(config, None))

    await _assert_stable(run, "bigquery", {"project": "p"}, min_docs=1)


async def test_snowflake() -> None:
    from app.ingestion.connectors.snowflake_connector import SnowflakeConnector
    from tests.ingestion.test_sdk_calls_off_loop import _fake_snowflake

    async def run(config: SourceConfig) -> list[RawDocument]:
        with patch.dict(sys.modules, _fake_snowflake()):
            return await _drain(SnowflakeConnector().get_delta(config, None))

    await _assert_stable(run, "snowflake", {"account": "a", "query": "SELECT 1"}, min_docs=1)


async def test_duckdb(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    from app.ingestion.connectors import duckdb_connector
    from app.ingestion.connectors.duckdb_connector import DuckDBConnector
    from tests.ingestion.test_sdk_calls_off_loop import _fake_duckdb

    monkeypatch.setattr(duckdb_connector, "_tenant_root", lambda tenant_id: tmp_path)

    async def run(config: SourceConfig) -> list[RawDocument]:
        with patch.dict(sys.modules, _fake_duckdb()):
            return await _drain(DuckDBConnector().get_delta(config, None))

    await _assert_stable(run, "duckdb", {"database": ":memory:", "query": "SELECT 1"}, min_docs=1)


async def test_mysql() -> None:
    from app.ingestion.connectors.mysql_connector import MySQLConnector

    def _conn() -> MagicMock:
        cur = MagicMock()
        cur.fetchall = MagicMock(return_value=[
            {"id": 1, "updated_at": "2026-01-01"}, {"id": 2, "updated_at": "2026-01-02"},
        ])
        conn = MagicMock()
        conn.cursor = MagicMock(return_value=cur)
        return conn

    async def run(config: SourceConfig) -> list[RawDocument]:
        with patch.object(MySQLConnector, "_connect", side_effect=lambda *a, **k: _conn()):
            return await _drain(MySQLConnector().get_delta(config, None))

    await _assert_stable(
        run, "mysql", {"host": "db.test", "table": "events", "cursor_column": "updated_at"}
    )


def test_keyless_rows_fall_back_to_a_row_hash() -> None:
    from app.ingestion.base_connector import row_identity, stable_doc_id

    cfg = _cfg("mysql", {}, "src-a")
    a = stable_doc_id(cfg, row_identity({"name": "x", "n": 1}))
    assert a == stable_doc_id(cfg, row_identity({"n": 1, "name": "x"}))
    assert a != stable_doc_id(cfg, row_identity({"name": "y", "n": 1}))
    # A configured key column wins over the conventional ones.
    assert row_identity({"id": 1, "sku": "Z"}, "sku") == "sku=Z"


async def test_gcs() -> None:
    from app.ingestion.connectors.gcs_connector import GCSConnector
    from tests.ingestion.test_sdk_calls_off_loop import _fake_gcs

    async def run(config: SourceConfig) -> list[RawDocument]:
        with patch.dict(sys.modules, _fake_gcs()):
            return await _drain(GCSConnector().get_delta(config, None))

    await _assert_stable(run, "gcs", {"bucket": "b"}, min_docs=1)


async def test_gdrive(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ingestion.connectors.gdrive_connector import GDriveSourceConnector
    from tests.ingestion.test_sdk_calls_off_loop import _fake_drive_client

    monkeypatch.setattr(GDriveSourceConnector, "_client", lambda self, cfg: _fake_drive_client())

    async def run(config: SourceConfig) -> list[RawDocument]:
        return await _drain(GDriveSourceConnector().get_delta(config, None))

    await _assert_stable(run, "gdrive", {"folder_id": "x"}, min_docs=1)


async def test_kinesis() -> None:
    from app.ingestion.connectors.kinesis_connector import KinesisConnector
    from tests.ingestion.test_sdk_calls_off_loop import _fake_kinesis_boto3

    async def run(config: SourceConfig) -> list[RawDocument]:
        with patch.dict(sys.modules, _fake_kinesis_boto3()):
            return await _drain(KinesisConnector().get_delta(config, None))

    await _assert_stable(run, "kinesis", {"stream_name": "s"}, min_docs=1)


async def test_pubsub() -> None:
    from app.ingestion.connectors.pubsub_connector import PubSubConnector
    from tests.ingestion.test_pubsub_connector import _fake_pubsub_modules, _make_message

    async def run(config: SourceConfig) -> list[RawDocument]:
        subscriber = MagicMock()
        resp = MagicMock()
        resp.received_messages = [
            _make_message("a1", b'{"x": 1}', {}, "msg-1"),
            _make_message("a2", b"plain", {}, "msg-2"),
        ]
        subscriber.pull = MagicMock(return_value=resp)
        with patch.dict(sys.modules, _fake_pubsub_modules(subscriber)):
            return await _drain(PubSubConnector().get_delta(config, None))

    await _assert_stable(run, "pubsub", {"project": "proj", "subscription": "sub"})


async def test_email_imap() -> None:
    from app.ingestion.connectors.email_imap_connector import EmailIMAPConnector
    from tests.ingestion.connectors.test_email_imap_connector import (
        _build_raw_email,
        _make_uid_side_effect,
    )

    async def run(config: SourceConfig) -> list[RawDocument]:
        conn = MagicMock()
        conn.uid.side_effect = _make_uid_side_effect(
            search_uids=[b"1", b"2"],
            messages={"1": _build_raw_email(subject="A"), "2": _build_raw_email(subject="B")},
        )
        with patch("imaplib.IMAP4_SSL", return_value=conn):
            return await _drain(EmailIMAPConnector().get_delta(config, None))

    await _assert_stable(run, "email_imap", {"host": "h", "mailbox": "INBOX"})


async def test_influxdb() -> None:
    from app.ingestion.connectors.influxdb_connector import InfluxDBConnector
    from tests.ingestion.connectors.test_influxdb_connector import (
        _FakeRecord,
        _FakeTable,
        _install_fake_influxdb_client,
    )

    async def run(config: SourceConfig) -> list[RawDocument]:
        records = [
            _FakeRecord({"_time": "2026-01-01", "_measurement": "cpu", "host": "a", "_value": 1}),
            _FakeRecord({"_time": "2026-01-01", "_measurement": "cpu", "host": "b", "_value": 2}),
        ]
        with patch.dict(sys.modules):
            _install_fake_influxdb_client(tables=[_FakeTable(records)])
            return await _drain(InfluxDBConnector().get_delta(config, None))

    ids = await _assert_stable(
        run,
        "influxdb",
        {"url": "http://influx:8086", "token": "t", "org": "o", "bucket": "b", "measurement": "cpu"},
    )
    assert len(ids) == 2  # same time, different tag -> different points


def _kafka_msg(offset: int, value: bytes) -> MagicMock:
    msg = MagicMock()
    msg.topic.return_value = "orders"
    msg.partition.return_value = 0
    msg.offset.return_value = offset
    msg.key.return_value = None
    msg.value.return_value = value
    msg.timestamp.return_value = None
    msg.error.return_value = None
    return msg


@pytest.mark.usefixtures("allow_unpinnable_drivers")
async def test_kafka() -> None:
    from app.ingestion.connectors.kafka_connector import KafkaConnector
    from tests.ingestion.test_connectors_round2 import _install_fake_confluent_kafka

    async def run(config: SourceConfig) -> list[RawDocument]:
        pkg, admin = _install_fake_confluent_kafka()
        consumer = MagicMock()
        consumer.poll.side_effect = [_kafka_msg(100, b'{"a": 1}'), _kafka_msg(101, b"x"), None]
        pkg.Consumer = MagicMock(return_value=consumer)
        with patch.dict(sys.modules, {"confluent_kafka": pkg, "confluent_kafka.admin": admin}):
            return await _drain(KafkaConnector().get_delta(config, "existing-cursor"))

    await _assert_stable(
        run, "kafka", {"topics": ["orders"], "bootstrap_servers": "b.test:9092", "batch_size": 10}
    )


async def test_mqtt() -> None:
    from app.ingestion.connectors.mqtt_connector import MQTTConnector
    from tests.ingestion.test_connectors_round2 import _install_fake_paho

    async def run(config: SourceConfig) -> list[RawDocument]:
        paho, mqtt, client_mod = _install_fake_paho()
        client = MagicMock()

        def _connect(host: str, port: int, keepalive: int) -> None:
            # A real broker always answers CONNECT with a CONNACK (USR-1).
            client.on_connect(client, None, {}, SimpleNamespace(is_failure=False), None)
            for topic, payload in (("s/temp", b'{"t": 21}'), ("s/hum", b"55%")):
                msg = MagicMock()
                msg.topic, msg.payload, msg.qos = topic, payload, 0
                client.on_message(client, None, msg)

        client.connect.side_effect = _connect
        client_mod.Client.return_value = client
        with patch.dict(
            sys.modules, {"paho": paho, "paho.mqtt": mqtt, "paho.mqtt.client": client_mod}
        ):
            return await _drain(MQTTConnector().get_delta(config, None))

    await _assert_stable(
        run, "mqtt", {"host": "mqtt.test", "topics": ["s/#"], "timeout_seconds": 0}
    )


async def test_neo4j() -> None:
    from app.ingestion.connectors.neo4j_connector import Neo4jConnector
    from tests.ingestion.test_connectors_round2 import _install_fake_neo4j

    class _Node:
        def __init__(self, node_id: int) -> None:
            self._properties = {"name": f"n{node_id}"}
            self.labels = ["Person"]
            self.id = node_id

    async def run(config: SourceConfig) -> list[RawDocument]:
        fake = _install_fake_neo4j()
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        session.run.return_value = [{"n": _Node(7)}, {"n": _Node(8)}]
        driver = MagicMock()
        driver.__enter__ = MagicMock(return_value=driver)
        driver.__exit__ = MagicMock(return_value=False)
        driver.session.return_value = session
        fake.GraphDatabase.driver.return_value = driver
        with patch.dict(sys.modules, {"neo4j": fake}):
            return await _drain(Neo4jConnector().get_delta(config, None))

    await _assert_stable(
        run, "neo4j", {"uri": "bolt://neo4j.test:7687", "node_labels": ["Person"]}
    )


async def test_notion() -> None:
    from app.ingestion.connectors.notion_connector import NotionSourceConnector
    from app.ingestion.connectors.notion_connector import NotionConnector

    pages = [
        {"id": "p1", "url": "u1", "last_edited_time": "2026-01-01", "properties": {}},
        {"id": "p2", "url": "u2", "last_edited_time": "2026-01-02", "properties": {}},
    ]

    async def run(config: SourceConfig) -> list[RawDocument]:
        with (
            patch.object(NotionConnector, "list_pages", new=AsyncMock(return_value=pages)),
            patch.object(NotionConnector, "fetch_page_content", new=AsyncMock(return_value="b")),
        ):
            return await _drain(NotionSourceConnector().get_delta(config, None))

    await _assert_stable(run, "notion", {"api_key": "k", "database_id": "db1"})


async def test_sharepoint() -> None:
    from app.ingestion.connectors.sharepoint_connector import SharePointSourceConnector
    from app.ingestion.connectors.sharepoint_connector import SharePointConnector

    files = [
        {"id": "item-1", "name": "a.txt", "lastModifiedDateTime": "2026-01-01"},
        {"id": "item-2", "name": "b.txt", "lastModifiedDateTime": "2026-01-02"},
    ]

    async def run(config: SourceConfig) -> list[RawDocument]:
        with (
            patch.object(SharePointConnector, "list_all_files", AsyncMock(return_value=files)),
            patch.object(SharePointConnector, "download_file", AsyncMock(return_value="text")),
        ):
            return await _drain(SharePointSourceConnector().get_delta(config, None))

    await _assert_stable(
        run, "sharepoint",
        {"site_id": "site-1", "tenant_id": "t", "client_id": "c", "client_secret": "s"},
    )


async def test_slack_messages_without_ts() -> None:
    from app.ingestion.connectors.slack_connector import SlackConnector
    from app.knowledge.ingestors import slack_ingestor as si_mod

    async def fake_ingest_channel(self: Any, channel_id: str, **_: Any) -> list[dict[str, Any]]:
        return [
            {"content": f"first in {channel_id}", "metadata": {}},
            {"content": f"second in {channel_id}", "metadata": {}},
        ]

    async def run(config: SourceConfig) -> list[RawDocument]:
        with patch.object(si_mod.SlackIngestor, "ingest_channel", fake_ingest_channel):
            return await _drain(SlackConnector().get_delta(config, None))

    await _assert_stable(run, "slack", {"bot_token": "t", "channels": ["C1"]})


async def test_github_chunks_without_a_source_doc_id() -> None:
    from app.ingestion.connectors.github_connector import GitHubConnector
    from app.knowledge.ingestors.github_ingestor import GitHubIngestor

    chunks = [
        {"content": "def a(): ...", "source_url": "https://g/a.py", "metadata": {}},
        {"content": "def b(): ...", "source_url": "https://g/b.py", "metadata": {}},
    ]

    async def run(config: SourceConfig) -> list[RawDocument]:
        with patch.object(GitHubIngestor, "ingest_repo", AsyncMock(return_value=chunks)):
            return await _drain(GitHubConnector().get_delta(config, None))

    await _assert_stable(run, "github", {"token": "t", "repos": ["o/r"]})


async def test_http_records_without_an_id() -> None:
    from app.ingestion.connectors.http_connector import HttpApiConnector
    from tests.ingestion.test_http_connector import _patch_httpx

    payload = {"results": [{"title": "a", "v": 1}, {"title": "b", "v": 2}]}

    async def run(config: SourceConfig) -> list[RawDocument]:
        with _patch_httpx(payload):
            return await _drain(HttpApiConnector().get_delta(config, None))

    await _assert_stable(run, "http", {"url": "https://1.1.1.1/api"})


async def test_agent_generated_goal_outputs_keep_their_ids() -> None:
    from datetime import UTC, datetime

    from app.ingestion.connectors.agent_generated_connector import AgentGeneratedConnector

    at = datetime(2026, 10, 5, tzinfo=UTC)
    rows = [
        {"_ts": at, "_id": gid, "goal_id": gid, "goal_text": f"goal {gid}", "agent_id": "",
         "completed_at": at.isoformat(), "answer": f"answer {gid}", "eval_score": None}
        for gid in ("g1", "g2")
    ]

    class _Fixed(AgentGeneratedConnector):
        async def _page_goals(self, *args: Any) -> list[dict[str, Any]]:  # type: ignore[override]
            after = args[3]
            return [r for r in rows if r["_id"] > after]

    async def run(config: SourceConfig) -> list[RawDocument]:
        return [d async for d, _ in _Fixed().get_delta(config, None)]

    await _assert_stable(run, "agent_generated", {"source_types": ["goal_output"]})


def test_no_connector_mints_random_document_ids() -> None:
    """Regression guard: ``uuid4`` must not appear in any connector's doc ids."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "app" / "ingestion" / "connectors"
    offenders = [
        f"{p.name}:{i}"
        for p in sorted(root.glob("*_connector.py"))
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if re.search(r"uuid\.?uuid4|uuid4\(\)", line)
    ]
    assert offenders == []


# ── object stores: authoritative listings for upstream deletions ──────────────


async def test_s3_live_listing_matches_its_document_ids() -> None:
    from app.ingestion.connectors.s3_connector import S3Connector
    from tests.ingestion.test_sdk_calls_off_loop import _fake_boto3

    cfg = _cfg("s3", {"bucket": "b"}, "src-a")
    with patch.dict(sys.modules, _fake_boto3()):
        docs = await _drain(S3Connector().get_delta(cfg, None))
        live = await S3Connector().list_live_doc_ids(cfg)
    assert live == {d.doc_id for d in docs} == {"s3://b/a.txt"}
    assert S3Connector().manages_doc_id("s3://b/a.txt")
    assert not S3Connector().manages_doc_id("3f2b0d0c-legacy")


async def test_gcs_live_listing_matches_its_document_ids() -> None:
    from app.ingestion.connectors.gcs_connector import GCSConnector
    from tests.ingestion.test_sdk_calls_off_loop import _fake_gcs

    cfg = _cfg("gcs", {"bucket": "b"}, "src-a")
    with patch.dict(sys.modules, _fake_gcs()):
        docs = await _drain(GCSConnector().get_delta(cfg, None))
    with patch.dict(sys.modules, _fake_gcs()):
        live = await GCSConnector().list_live_doc_ids(cfg)
    assert live == {d.doc_id for d in docs}
    assert len(live or ()) == 1


async def test_azure_live_listing_matches_its_document_ids() -> None:
    from app.ingestion.connectors.azure_blob_connector import AzureBlobConnector
    from tests.ingestion.test_sdk_calls_off_loop import _AZ, _fake_azure

    cfg = _cfg("azure_blob", _AZ, "src-a")
    with patch.dict(sys.modules, _fake_azure()):
        docs = await _drain(AzureBlobConnector().get_delta(cfg, None))
    with patch.dict(sys.modules, _fake_azure()):
        live = await AzureBlobConnector().list_live_doc_ids(cfg)
    assert live == {d.doc_id for d in docs}
    assert len(live or ()) == 1


async def test_connectors_without_a_listing_cannot_delete() -> None:
    from app.ingestion.connectors.jira_connector import JiraConnector

    assert await JiraConnector().list_live_doc_ids(_cfg("jira", {}, "s")) is None


# ── KB-44: the live listing streams page by page ──────────────────────────────


async def test_s3_live_listing_streams_pages_lazily() -> None:
    """The reconciler stages ids as pages arrive: the S3 listing must never pull
    the whole bucket before yielding its first id."""
    from app.ingestion.connectors.s3_connector import S3Connector
    from tests.ingestion.test_sdk_calls_off_loop import _fake_boto3

    pulled: list[int] = []

    def _pages(**_k: Any) -> Any:
        for page in range(1000):
            pulled.append(page)
            yield {"Contents": [{"Key": f"p{page}-{i}.txt"} for i in range(1000)]}

    modules = _fake_boto3()
    s3 = modules["boto3"].Session.return_value.client.side_effect(  # type: ignore[attr-defined]
        "s3"
    )
    s3.get_paginator.return_value.paginate.side_effect = _pages
    cfg = _cfg("s3", {"bucket": "b"}, "src-a")
    with patch.dict(sys.modules, modules):
        stream = S3Connector().iter_live_doc_ids(cfg)
        first = [await stream.__anext__() for _ in range(1500)]
        await stream.aclose()
    assert first[0] == "s3://b/p0-0.txt" and first[-1] == "s3://b/p1-499.txt"
    assert len(pulled) <= 3  # two pages consumed (plus at most one read ahead)


async def test_only_connectors_with_an_upstream_listing_can_reconcile() -> None:
    from app.ingestion.base_connector import lists_upstream
    from app.ingestion.connectors.azure_blob_connector import AzureBlobConnector
    from app.ingestion.connectors.gcs_connector import GCSConnector
    from app.ingestion.connectors.jira_connector import JiraConnector
    from app.ingestion.connectors.s3_connector import S3Connector

    assert lists_upstream(S3Connector())
    assert lists_upstream(GCSConnector())
    assert lists_upstream(AzureBlobConnector())
    assert not lists_upstream(JiraConnector())
