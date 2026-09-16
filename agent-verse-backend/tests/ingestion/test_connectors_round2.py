"""Tests for low-coverage ingestion connectors (round 2).

Covers: JiraConnector, ZendeskConnector, MQTTConnector, BigQueryConnector,
GitLabConnector, Neo4jConnector, KafkaConnector.

All external SDKs (confluent-kafka, paho-mqtt, neo4j, google-cloud-bigquery)
are not installed in the test environment, so their modules are faked via
`sys.modules` patching. httpx-based connectors mock `httpx.AsyncClient`
directly, following the existing pattern in test_new_connectors.py.
"""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

from app.ingestion.source_config import SourceConfig


def _make_config(source_type: str, conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-test",
        tenant_id="t1",
        name="Test",
        family="database",
        source_type=source_type,
        enabled=True,
        sync_mode="incremental",
        connection_config=conn_config or {},
    )


async def _collect_async(agen) -> list:
    results = []
    async for item in agen:
        results.append(item)
    return results


def _mock_httpx_client(get_responses=None, get_side_effect=None):
    """Return a mock httpx.AsyncClient class configured for `async with`."""
    mock_client = AsyncMock()
    if get_side_effect is not None:
        mock_client.get = AsyncMock(side_effect=get_side_effect)
    elif get_responses is not None:
        mock_client.get = AsyncMock(side_effect=get_responses)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _resp(json_data=None, is_success=True, status_code=200, text="", links=None):
    r = MagicMock()
    r.is_success = is_success
    r.status_code = status_code
    r.text = text
    r.links = links or {}
    r.raise_for_status = MagicMock()
    if not is_success:
        import httpx

        def _raise(*a, **k):
            raise httpx.HTTPStatusError("error", request=MagicMock(), response=r)

        r.raise_for_status = MagicMock(side_effect=_raise)
    if json_data is not None:
        r.json = MagicMock(return_value=json_data)
    return r


# ── JiraConnector ─────────────────────────────────────────────────────────────

class TestJiraConnector:
    def test_register(self):
        import importlib

        from app.ingestion.connector_registry import get_connector

        importlib.import_module("app.ingestion.connectors.jira_connector")
        assert get_connector("jira") is not None

    def test_supports_acl_propagation(self):
        from app.ingestion.connectors.jira_connector import JiraConnector

        assert JiraConnector.supports_acl_propagation is True

    def test_validate_connection_success(self):
        from app.ingestion.connectors.jira_connector import JiraConnector

        config = _make_config(
            "jira",
            {"base_url": "https://foo.atlassian.net", "username": "u", "api_token": "t"},
        )
        connector = JiraConnector()
        resp = _resp({"displayName": "Alice", "emailAddress": "a@example.com"})

        mock_client = _mock_httpx_client(get_responses=[resp])
        with patch("httpx.AsyncClient", return_value=mock_client):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is True
        assert health.metadata["user"] == "Alice"

    def test_validate_connection_error(self):
        from app.ingestion.connectors.jira_connector import JiraConnector

        config = _make_config("jira", {"base_url": "https://foo.atlassian.net"})
        connector = JiraConnector()

        mock_client = _mock_httpx_client(get_side_effect=RuntimeError("boom"))
        with patch("httpx.AsyncClient", return_value=mock_client):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is False
        assert "boom" in health.error

    def test_get_delta_fetches_issues_with_comments(self):
        from app.ingestion.connectors.jira_connector import JiraConnector

        config = _make_config(
            "jira",
            {
                "base_url": "https://foo.atlassian.net",
                "project_keys": ["ABC"],
                "batch_size": 50,
                "include_comments": True,
            },
        )
        connector = JiraConnector()

        issue = {
            "key": "ABC-1",
            "fields": {
                "summary": "Bug happens",
                "description": {
                    "type": "doc",
                    "content": [{"type": "text", "text": "Details here"}],
                },
                "status": {"name": "Open"},
                "assignee": {"displayName": "Bob"},
                "issuetype": {"name": "Bug"},
                "updated": "2026-01-02T00:00:00.000Z",
                "comment": {
                    "comments": [
                        {"author": {"displayName": "Carol"}, "body": "looks bad"},
                    ]
                },
            },
        }
        page1 = _resp({"issues": [issue], "total": 1})

        mock_client = _mock_httpx_client(get_responses=[page1])
        with patch("httpx.AsyncClient", return_value=mock_client):
            docs = list(asyncio.run(_collect_async(connector.get_delta(config, None))))

        assert len(docs) == 1
        doc, cursor = docs[0]
        assert b"ABC-1" in doc.content
        assert b"Details here" in doc.content
        assert b"Comment by Carol" in doc.content
        assert cursor == "2026-01-02T00:00:00.000Z"

    def test_get_delta_stops_on_failed_response(self):
        from app.ingestion.connectors.jira_connector import JiraConnector

        config = _make_config("jira", {"base_url": "https://foo.atlassian.net"})
        connector = JiraConnector()

        mock_client = _mock_httpx_client(get_responses=[_resp(is_success=False, status_code=500)])
        with patch("httpx.AsyncClient", return_value=mock_client):
            docs = list(asyncio.run(_collect_async(connector.get_delta(config, None))))

        assert docs == []

    def test_get_delta_no_more_issues_breaks(self):
        from app.ingestion.connectors.jira_connector import JiraConnector

        config = _make_config("jira", {"base_url": "https://foo.atlassian.net"})
        connector = JiraConnector()

        mock_client = _mock_httpx_client(get_responses=[_resp({"issues": [], "total": 0})])
        with patch("httpx.AsyncClient", return_value=mock_client):
            docs = list(asyncio.run(_collect_async(connector.get_delta(config, None))))

        assert docs == []

    def test_extract_adf_text_variants(self):
        from app.ingestion.connectors.jira_connector import _extract_adf_text

        assert _extract_adf_text(None) == ""
        assert _extract_adf_text("plain") == "plain"
        assert _extract_adf_text(123) == ""
        nested = {
            "type": "doc",
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "paragraph", "content": [{"type": "text", "text": "World"}]},
            ],
        }
        assert _extract_adf_text(nested) == "Hello World"


# ── ZendeskConnector ──────────────────────────────────────────────────────────

class TestZendeskConnector:
    def test_register(self):
        import importlib

        from app.ingestion.connector_registry import get_connector

        importlib.import_module("app.ingestion.connectors.zendesk_connector")
        assert get_connector("zendesk") is not None

    def test_validate_connection_success(self):
        from app.ingestion.connectors.zendesk_connector import ZendeskConnector

        config = _make_config(
            "zendesk", {"subdomain": "acme", "email": "a@acme.com", "api_token": "tok"}
        )
        connector = ZendeskConnector()
        resp = _resp({"user": {"name": "Dana"}})

        mock_client = _mock_httpx_client(get_responses=[resp])
        with patch("httpx.AsyncClient", return_value=mock_client):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is True
        assert health.metadata["user"] == "Dana"
        assert health.metadata["subdomain"] == "acme"

    def test_validate_connection_error(self):
        from app.ingestion.connectors.zendesk_connector import ZendeskConnector

        config = _make_config("zendesk", {"subdomain": "acme"})
        connector = ZendeskConnector()

        mock_client = _mock_httpx_client(get_side_effect=RuntimeError("nope"))
        with patch("httpx.AsyncClient", return_value=mock_client):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is False
        assert "nope" in health.error

    def test_get_delta_tickets(self):
        from app.ingestion.connectors.zendesk_connector import ZendeskConnector

        config = _make_config(
            "zendesk",
            {"subdomain": "acme", "email": "a@acme.com", "api_token": "tok", "ingest_types": ["tickets"]},
        )
        connector = ZendeskConnector()

        page = _resp(
            {
                "tickets": [
                    {
                        "id": 1,
                        "subject": "Help",
                        "status": "open",
                        "priority": "high",
                        "updated_at": "2026-01-01T00:00:00Z",
                        "description": "I need help",
                    },
                    {"id": 2, "status": "deleted"},
                ],
                "end_time": 1700000000,
                "end_of_stream": True,
            }
        )

        mock_client = _mock_httpx_client(get_responses=[page])
        with patch("httpx.AsyncClient", return_value=mock_client):
            docs = list(asyncio.run(_collect_async(connector.get_delta(config, None))))

        assert len(docs) == 1
        doc, cursor = docs[0]
        assert b"Ticket #1" in doc.content
        assert cursor == "1700000000"

    def test_get_delta_articles(self):
        from app.ingestion.connectors.zendesk_connector import ZendeskConnector

        config = _make_config(
            "zendesk",
            {"subdomain": "acme", "email": "a@acme.com", "api_token": "tok", "ingest_types": ["articles"]},
        )
        connector = ZendeskConnector()

        page = _resp(
            {
                "articles": [
                    {
                        "id": 9,
                        "title": "How to reset",
                        "body": "<p>Click <b>here</b></p>",
                        "updated_at": "2026-02-01T00:00:00Z",
                        "html_url": "https://acme.zendesk.com/hc/en-us/articles/9",
                    }
                ],
                "next_page": None,
            }
        )

        mock_client = _mock_httpx_client(get_responses=[page])
        with patch("httpx.AsyncClient", return_value=mock_client):
            docs = list(asyncio.run(_collect_async(connector.get_delta(config, None))))

        assert len(docs) == 1
        doc, cursor = docs[0]
        assert b"How to reset" in doc.content
        assert b"<b>" not in doc.content
        assert cursor == "2026-02-01T00:00:00Z"

    def test_get_delta_tickets_failed_response_breaks(self):
        from app.ingestion.connectors.zendesk_connector import ZendeskConnector

        config = _make_config(
            "zendesk",
            {"subdomain": "acme", "ingest_types": ["tickets"]},
        )
        connector = ZendeskConnector()

        mock_client = _mock_httpx_client(get_responses=[_resp(is_success=False)])
        with patch("httpx.AsyncClient", return_value=mock_client):
            docs = list(asyncio.run(_collect_async(connector.get_delta(config, None))))

        assert docs == []


# ── GitLabConnector ───────────────────────────────────────────────────────────

class TestGitLabConnector:
    def test_register(self):
        import importlib

        from app.ingestion.connector_registry import get_connector

        importlib.import_module("app.ingestion.connectors.gitlab_connector")
        assert get_connector("gitlab") is not None

    def test_supports_acl_propagation(self):
        from app.ingestion.connectors.gitlab_connector import GitLabConnector

        assert GitLabConnector.supports_acl_propagation is True

    def test_validate_connection_success(self):
        from app.ingestion.connectors.gitlab_connector import GitLabConnector

        config = _make_config("gitlab", {"base_url": "https://gitlab.com", "token": "tok"})
        connector = GitLabConnector()
        resp = _resp({"username": "eve", "name": "Eve"})

        mock_client = _mock_httpx_client(get_responses=[resp])
        with patch("httpx.AsyncClient", return_value=mock_client):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is True
        assert health.metadata["user"] == "eve"

    def test_validate_connection_error(self):
        from app.ingestion.connectors.gitlab_connector import GitLabConnector

        config = _make_config("gitlab", {})
        connector = GitLabConnector()

        mock_client = _mock_httpx_client(get_side_effect=RuntimeError("down"))
        with patch("httpx.AsyncClient", return_value=mock_client):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is False

    def test_get_delta_issues_and_mrs(self):
        from app.ingestion.connectors.gitlab_connector import GitLabConnector

        config = _make_config(
            "gitlab",
            {
                "base_url": "https://gitlab.com",
                "token": "tok",
                "project_ids": [42],
                "ingest_types": ["issues", "merge_requests"],
            },
        )
        connector = GitLabConnector()

        issues_resp = _resp(
            [
                {
                    "iid": 1,
                    "title": "Bug",
                    "state": "opened",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "description": "desc",
                    "web_url": "https://gitlab.com/p/42/issues/1",
                }
            ]
        )
        mrs_resp = _resp(
            [
                {
                    "iid": 5,
                    "title": "Fix",
                    "state": "merged",
                    "source_branch": "feat",
                    "target_branch": "main",
                    "updated_at": "2026-01-02T00:00:00Z",
                    "description": "mr desc",
                    "web_url": "https://gitlab.com/p/42/merge_requests/5",
                }
            ]
        )

        mock_client = _mock_httpx_client(get_responses=[issues_resp, mrs_resp])
        with patch("httpx.AsyncClient", return_value=mock_client):
            docs = list(asyncio.run(_collect_async(connector.get_delta(config, None))))

        assert len(docs) == 2
        types = {d[0].metadata["type"] for d in docs}
        assert types == {"issue", "merge_request"}

    def test_get_delta_failed_response_breaks(self):
        from app.ingestion.connectors.gitlab_connector import GitLabConnector

        config = _make_config(
            "gitlab",
            {"base_url": "https://gitlab.com", "project_ids": [1], "ingest_types": ["issues"]},
        )
        connector = GitLabConnector()

        mock_client = _mock_httpx_client(get_responses=[_resp(is_success=False)])
        with patch("httpx.AsyncClient", return_value=mock_client):
            docs = list(asyncio.run(_collect_async(connector.get_delta(config, None))))

        assert docs == []


# ── KafkaConnector (fake confluent_kafka module) ─────────────────────────────

def _install_fake_confluent_kafka():
    """Install a minimal fake `confluent_kafka` package into sys.modules."""
    fake_pkg = ModuleType("confluent_kafka")
    fake_admin = ModuleType("confluent_kafka.admin")

    class FakeKafkaError:
        _PARTITION_EOF = -191

    fake_pkg.KafkaError = FakeKafkaError
    fake_pkg.Consumer = MagicMock()
    fake_admin.AdminClient = MagicMock()
    fake_pkg.admin = fake_admin
    return fake_pkg, fake_admin


class TestKafkaConnector:
    def test_register(self):
        import importlib

        from app.ingestion.connector_registry import get_connector

        importlib.import_module("app.ingestion.connectors.kafka_connector")
        assert get_connector("kafka") is not None

    def test_supports_streaming(self):
        from app.ingestion.connectors.kafka_connector import KafkaConnector

        assert KafkaConnector.supports_streaming is True

    def test_validate_connection_not_installed(self):
        from app.ingestion.connectors.kafka_connector import KafkaConnector

        config = _make_config("kafka", {"bootstrap_servers": "localhost:9092"})
        connector = KafkaConnector()

        with patch.dict(sys.modules, {"confluent_kafka": None, "confluent_kafka.admin": None}):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is False
        assert "confluent-kafka" in health.error

    def test_validate_connection_success(self):
        from app.ingestion.connectors.kafka_connector import KafkaConnector

        fake_pkg, fake_admin = _install_fake_confluent_kafka()
        metadata = MagicMock()
        metadata.brokers = {1: "b1"}
        metadata.topics = {"t1": "x"}
        fake_admin.AdminClient.return_value.list_topics.return_value = metadata

        config = _make_config("kafka", {"bootstrap_servers": "localhost:9092"})
        connector = KafkaConnector()

        with patch.dict(sys.modules, {"confluent_kafka": fake_pkg, "confluent_kafka.admin": fake_admin}):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is True
        assert health.metadata["brokers"] == 1
        assert health.metadata["topics"] == 1

    def test_validate_connection_error(self):
        from app.ingestion.connectors.kafka_connector import KafkaConnector

        fake_pkg, fake_admin = _install_fake_confluent_kafka()
        fake_admin.AdminClient.side_effect = RuntimeError("conn refused")

        config = _make_config("kafka", {})
        connector = KafkaConnector()

        with patch.dict(sys.modules, {"confluent_kafka": fake_pkg, "confluent_kafka.admin": fake_admin}):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is False
        assert "conn refused" in health.error

    def test_get_delta_not_installed_yields_nothing(self):
        from app.ingestion.connectors.kafka_connector import KafkaConnector

        config = _make_config("kafka", {"topics": ["t1"]})
        connector = KafkaConnector()

        with patch.dict(sys.modules, {"confluent_kafka": None}):
            docs = list(asyncio.run(_collect_async(connector.get_delta(config, None))))

        assert docs == []

    def test_get_delta_yields_messages(self):
        from app.ingestion.connectors.kafka_connector import KafkaConnector

        fake_pkg, fake_admin = _install_fake_confluent_kafka()

        msg1 = MagicMock()
        msg1.topic.return_value = "orders"
        msg1.partition.return_value = 0
        msg1.offset.return_value = 100
        msg1.key.return_value = b"key1"
        msg1.value.return_value = b'{"order_id": 1}'
        msg1.timestamp.return_value = (1, 123456)
        msg1.error.return_value = None

        msg2 = MagicMock()
        msg2.topic.return_value = "orders"
        msg2.partition.return_value = 0
        msg2.offset.return_value = 101
        msg2.key.return_value = None
        msg2.value.return_value = b"plain text"
        msg2.timestamp.return_value = None
        msg2.error.return_value = None

        fake_consumer_instance = MagicMock()
        fake_consumer_instance.poll.side_effect = [msg1, msg2, None]
        fake_pkg.Consumer = MagicMock(return_value=fake_consumer_instance)

        config = _make_config(
            "kafka", {"topics": ["orders"], "bootstrap_servers": "b:9092", "batch_size": 10}
        )
        connector = KafkaConnector()

        with patch.dict(sys.modules, {"confluent_kafka": fake_pkg, "confluent_kafka.admin": fake_admin}):
            docs = list(asyncio.run(_collect_async(connector.get_delta(config, "existing-cursor"))))

        assert len(docs) == 2
        doc1, cursor1 = docs[0]
        assert doc1.metadata["topic"] == "orders"
        assert doc1.content_type == "application/json"
        assert cursor1 == "orders:0:100"
        doc2, cursor2 = docs[1]
        assert doc2.content_type == "text/plain"
        assert cursor2 == "orders:0:101"
        fake_consumer_instance.commit.assert_called_once()
        fake_consumer_instance.close.assert_called_once()


# ── MQTTConnector (fake paho module) ─────────────────────────────────────────

def _install_fake_paho():
    fake_paho = ModuleType("paho")
    fake_mqtt = ModuleType("paho.mqtt")
    fake_client_mod = ModuleType("paho.mqtt.client")
    fake_client_mod.Client = MagicMock()
    fake_mqtt.client = fake_client_mod
    fake_paho.mqtt = fake_mqtt
    return fake_paho, fake_mqtt, fake_client_mod


class TestMQTTConnector:
    def test_register(self):
        import importlib

        from app.ingestion.connector_registry import get_connector

        importlib.import_module("app.ingestion.connectors.mqtt_connector")
        assert get_connector("mqtt") is not None

    def test_supports_streaming(self):
        from app.ingestion.connectors.mqtt_connector import MQTTConnector

        assert MQTTConnector.supports_streaming is True

    def test_validate_connection_not_installed(self):
        from app.ingestion.connectors.mqtt_connector import MQTTConnector

        config = _make_config("mqtt", {"host": "localhost"})
        connector = MQTTConnector()

        with patch.dict(sys.modules, {"paho": None, "paho.mqtt": None, "paho.mqtt.client": None}):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is False
        assert "paho-mqtt" in health.error

    def test_validate_connection_success(self):
        from app.ingestion.connectors.mqtt_connector import MQTTConnector

        fake_paho, fake_mqtt, fake_client_mod = _install_fake_paho()

        client_instance = MagicMock()

        def fake_connect_async(host, port, keepalive):
            # Immediately mark connected via the on_connect callback.
            client_instance.on_connect(client_instance, None, None, 0)

        client_instance.connect_async.side_effect = fake_connect_async
        fake_client_mod.Client.return_value = client_instance

        config = _make_config("mqtt", {"host": "localhost", "port": 1883, "username": "u"})
        connector = MQTTConnector()

        with patch.dict(
            sys.modules,
            {"paho": fake_paho, "paho.mqtt": fake_mqtt, "paho.mqtt.client": fake_client_mod},
        ):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is True
        assert health.metadata["host"] == "localhost"
        client_instance.username_pw_set.assert_called_once()

    def test_validate_connection_timeout(self):
        from app.ingestion.connectors.mqtt_connector import MQTTConnector

        fake_paho, fake_mqtt, fake_client_mod = _install_fake_paho()
        client_instance = MagicMock()
        fake_client_mod.Client.return_value = client_instance

        config = _make_config("mqtt", {"host": "localhost"})
        connector = MQTTConnector()

        with patch.dict(
            sys.modules,
            {"paho": fake_paho, "paho.mqtt": fake_mqtt, "paho.mqtt.client": fake_client_mod},
        ):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is False
        assert "timed out" in health.error

    def test_validate_connection_error(self):
        from app.ingestion.connectors.mqtt_connector import MQTTConnector

        fake_paho, fake_mqtt, fake_client_mod = _install_fake_paho()
        fake_client_mod.Client.side_effect = RuntimeError("bad host")

        config = _make_config("mqtt", {})
        connector = MQTTConnector()

        with patch.dict(
            sys.modules,
            {"paho": fake_paho, "paho.mqtt": fake_mqtt, "paho.mqtt.client": fake_client_mod},
        ):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is False
        assert "bad host" in health.error

    def test_get_delta_not_installed_yields_nothing(self):
        from app.ingestion.connectors.mqtt_connector import MQTTConnector

        config = _make_config("mqtt", {})
        connector = MQTTConnector()

        with patch.dict(sys.modules, {"paho": None, "paho.mqtt": None, "paho.mqtt.client": None}):
            docs = list(asyncio.run(_collect_async(connector.get_delta(config, None))))

        assert docs == []

    def test_get_delta_yields_messages(self):
        from app.ingestion.connectors.mqtt_connector import MQTTConnector

        fake_paho, fake_mqtt, fake_client_mod = _install_fake_paho()
        client_instance = MagicMock()

        def fake_connect(host, port, keepalive):
            msg1 = MagicMock()
            msg1.topic = "sensors/temp"
            msg1.payload = b'{"temp": 21.5}'
            msg1.qos = 0
            msg2 = MagicMock()
            msg2.topic = "sensors/hum"
            msg2.payload = b"55%"
            msg2.qos = 1
            client_instance.on_message(client_instance, None, msg1)
            client_instance.on_message(client_instance, None, msg2)

        client_instance.connect.side_effect = fake_connect
        fake_client_mod.Client.return_value = client_instance

        config = _make_config(
            "mqtt", {"host": "localhost", "topics": ["sensors/#"], "timeout_seconds": 0}
        )
        connector = MQTTConnector()

        with patch.dict(
            sys.modules,
            {"paho": fake_paho, "paho.mqtt": fake_mqtt, "paho.mqtt.client": fake_client_mod},
        ):
            docs = list(asyncio.run(_collect_async(connector.get_delta(config, None))))

        assert len(docs) == 2
        doc1, _ = docs[0]
        assert doc1.content_type == "application/json"
        doc2, _ = docs[1]
        assert doc2.content_type == "text/plain"


# ── Neo4jConnector (fake neo4j module) ───────────────────────────────────────

def _install_fake_neo4j():
    fake_neo4j = ModuleType("neo4j")
    fake_neo4j.GraphDatabase = MagicMock()
    return fake_neo4j


class TestNeo4jConnector:
    def test_register(self):
        import importlib

        from app.ingestion.connector_registry import get_connector

        importlib.import_module("app.ingestion.connectors.neo4j_connector")
        assert get_connector("neo4j") is not None

    def test_validate_connection_not_installed(self):
        from app.ingestion.connectors.neo4j_connector import Neo4jConnector

        config = _make_config("neo4j", {})
        connector = Neo4jConnector()

        with patch.dict(sys.modules, {"neo4j": None}):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is False
        assert "neo4j" in health.error

    def test_validate_connection_success(self):
        from app.ingestion.connectors.neo4j_connector import Neo4jConnector

        fake_neo4j = _install_fake_neo4j()
        driver_cm = MagicMock()
        driver_cm.__enter__ = MagicMock(return_value=driver_cm)
        driver_cm.__exit__ = MagicMock(return_value=False)
        fake_neo4j.GraphDatabase.driver.return_value = driver_cm

        config = _make_config("neo4j", {"uri": "bolt://localhost:7687"})
        connector = Neo4jConnector()

        with patch.dict(sys.modules, {"neo4j": fake_neo4j}):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is True
        driver_cm.verify_connectivity.assert_called_once()

    def test_validate_connection_error(self):
        from app.ingestion.connectors.neo4j_connector import Neo4jConnector

        fake_neo4j = _install_fake_neo4j()
        fake_neo4j.GraphDatabase.driver.side_effect = RuntimeError("unreachable")

        config = _make_config("neo4j", {})
        connector = Neo4jConnector()

        with patch.dict(sys.modules, {"neo4j": fake_neo4j}):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is False
        assert "unreachable" in health.error

    def test_get_delta_not_installed_yields_nothing(self):
        from app.ingestion.connectors.neo4j_connector import Neo4jConnector

        config = _make_config("neo4j", {})
        connector = Neo4jConnector()

        with patch.dict(sys.modules, {"neo4j": None}):
            docs = list(asyncio.run(_collect_async(connector.get_delta(config, None))))

        assert docs == []

    def test_get_delta_yields_nodes(self):
        from app.ingestion.connectors.neo4j_connector import Neo4jConnector

        fake_neo4j = _install_fake_neo4j()

        class FakeNode:
            def __init__(self, props, labels, node_id):
                self._properties = props
                self.labels = labels
                self.id = node_id

        node = FakeNode({"name": "Alice", "id": 7}, ["Person"], 7)
        record = {"n": node}

        session_cm = MagicMock()
        session_cm.__enter__ = MagicMock(return_value=session_cm)
        session_cm.__exit__ = MagicMock(return_value=False)
        session_cm.run.return_value = [record]

        driver_cm = MagicMock()
        driver_cm.__enter__ = MagicMock(return_value=driver_cm)
        driver_cm.__exit__ = MagicMock(return_value=False)
        driver_cm.session.return_value = session_cm

        fake_neo4j.GraphDatabase.driver.return_value = driver_cm

        config = _make_config("neo4j", {"uri": "bolt://localhost:7687", "node_labels": ["Person"]})
        connector = Neo4jConnector()

        with patch.dict(sys.modules, {"neo4j": fake_neo4j}):
            docs = list(asyncio.run(_collect_async(connector.get_delta(config, None))))

        assert len(docs) == 1
        doc, cursor = docs[0]
        assert b"Person" in doc.content
        assert b"Alice" in doc.content
        assert cursor == "7"


# ── BigQueryConnector (fake google.cloud.bigquery module) ────────────────────

def _install_fake_bigquery():
    fake_google = ModuleType("google")
    fake_cloud = ModuleType("google.cloud")
    fake_bq = ModuleType("google.cloud.bigquery")
    fake_bq.Client = MagicMock()
    fake_cloud.bigquery = fake_bq
    fake_google.cloud = fake_cloud
    return fake_google, fake_cloud, fake_bq


class TestBigQueryConnector:
    def test_register(self):
        import importlib

        from app.ingestion.connector_registry import get_connector

        importlib.import_module("app.ingestion.connectors.bigquery_connector")
        assert get_connector("bigquery") is not None

    def test_validate_connection_not_installed(self):
        from app.ingestion.connectors.bigquery_connector import BigQueryConnector

        config = _make_config("bigquery", {})
        connector = BigQueryConnector()

        with patch.dict(
            sys.modules, {"google.cloud": None, "google.cloud.bigquery": None}
        ):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is False
        assert "bigquery" in health.error

    def test_validate_connection_success(self):
        from app.ingestion.connectors.bigquery_connector import BigQueryConnector

        fake_google, fake_cloud, fake_bq = _install_fake_bigquery()
        fake_bq.Client.return_value.list_datasets.return_value = iter([MagicMock()])

        config = _make_config("bigquery", {"project": "proj1"})
        connector = BigQueryConnector()

        with patch.dict(
            sys.modules,
            {"google": fake_google, "google.cloud": fake_cloud, "google.cloud.bigquery": fake_bq},
        ):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is True
        assert health.metadata["project"] == "proj1"

    def test_validate_connection_with_service_account_json(self):
        from app.ingestion.connectors.bigquery_connector import BigQueryConnector

        fake_google, fake_cloud, fake_bq = _install_fake_bigquery()
        fake_bq.Client.from_service_account_json.return_value.list_datasets.return_value = iter([])

        config = _make_config(
            "bigquery", {"project": "proj1", "service_account_json": {"type": "service_account"}}
        )
        connector = BigQueryConnector()

        with patch.dict(
            sys.modules,
            {"google": fake_google, "google.cloud": fake_cloud, "google.cloud.bigquery": fake_bq},
        ):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is True
        fake_bq.Client.from_service_account_json.assert_called_once()

    def test_validate_connection_error(self):
        from app.ingestion.connectors.bigquery_connector import BigQueryConnector

        fake_google, fake_cloud, fake_bq = _install_fake_bigquery()
        fake_bq.Client.side_effect = RuntimeError("denied")

        config = _make_config("bigquery", {"project": "proj1"})
        connector = BigQueryConnector()

        with patch.dict(
            sys.modules,
            {"google": fake_google, "google.cloud": fake_cloud, "google.cloud.bigquery": fake_bq},
        ):
            health = asyncio.run(connector.validate_connection(config))

        assert health.ok is False
        assert "denied" in health.error

    def test_get_delta_not_installed_yields_nothing(self):
        from app.ingestion.connectors.bigquery_connector import BigQueryConnector

        config = _make_config("bigquery", {})
        connector = BigQueryConnector()

        with patch.dict(sys.modules, {"google.cloud": None, "google.cloud.bigquery": None}):
            docs = list(asyncio.run(_collect_async(connector.get_delta(config, None))))

        assert docs == []

    def test_get_delta_query_mode(self):
        from app.ingestion.connectors.bigquery_connector import BigQueryConnector

        fake_google, fake_cloud, fake_bq = _install_fake_bigquery()
        row1 = {"id": 1, "updated_at": "2026-01-01", "name": "widget"}
        query_result = MagicMock()
        query_result.result.return_value = [row1]
        fake_bq.Client.return_value.query.return_value = query_result

        config = _make_config(
            "bigquery",
            {"project": "proj1", "mode": "query", "query": "SELECT * FROM t", "cursor_column": "updated_at"},
        )
        connector = BigQueryConnector()

        with patch.dict(
            sys.modules,
            {"google": fake_google, "google.cloud": fake_cloud, "google.cloud.bigquery": fake_bq},
        ):
            docs = list(asyncio.run(_collect_async(connector.get_delta(config, None))))

        assert len(docs) == 1
        doc, cursor = docs[0]
        assert b"widget" in doc.content
        assert cursor == "2026-01-01"

    def test_get_delta_table_mode_with_cursor(self):
        from app.ingestion.connectors.bigquery_connector import BigQueryConnector

        fake_google, fake_cloud, fake_bq = _install_fake_bigquery()
        row1 = {"id": 2, "updated_at": "2026-02-02", "name": "gadget"}
        query_result = MagicMock()
        query_result.result.return_value = [row1]
        client_instance = MagicMock()
        client_instance.query.return_value = query_result
        fake_bq.Client.return_value = client_instance

        config = _make_config(
            "bigquery",
            {"project": "proj1", "mode": "table", "table": "ds.tbl", "cursor_column": "updated_at"},
        )
        connector = BigQueryConnector()

        with patch.dict(
            sys.modules,
            {"google": fake_google, "google.cloud": fake_cloud, "google.cloud.bigquery": fake_bq},
        ):
            docs = list(asyncio.run(_collect_async(connector.get_delta(config, "2026-01-01"))))

        assert len(docs) == 1
        sql_used = client_instance.query.call_args[0][0]
        assert "WHERE updated_at > '2026-01-01'" in sql_used
        assert "ORDER BY updated_at LIMIT" in sql_used
