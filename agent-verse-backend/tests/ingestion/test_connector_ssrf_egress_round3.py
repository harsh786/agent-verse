"""SSRF round 3: the streaming / mail connectors that dial a tenant host directly.

Kafka (``bootstrap_servers`` + the broker addresses the cluster *advertises*),
MQTT (``host``) and IMAP (``host``) used to connect wherever the tenant said —
Kafka and MQTT even defaulted to ``localhost`` — so a source pointed at the
platform's own loopback / VPC / metadata service was a network-pivot primitive.

Targets are literal internal IPs so no DNS is needed. Fake client libraries are
installed so the guard (not an ImportError) is what stops the connection.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion.connector_egress import ConnectorEgressBlockedError
from app.ingestion.source_config import SourceConfig, SourceFamily


def _config(source_type: str, cc: dict[str, Any]) -> SourceConfig:
    return SourceConfig(
        source_id=f"src-{source_type}",
        tenant_id="tenant-ssrf",
        name=source_type,
        family=SourceFamily.WEB,
        source_type=source_type,
        collection_id="col-1",
        connection_config=cc,
    )


def _fake_kafka() -> tuple[dict[str, ModuleType], MagicMock, MagicMock]:
    pkg = ModuleType("confluent_kafka")
    admin = ModuleType("confluent_kafka.admin")

    class _KafkaError:
        _PARTITION_EOF = -191

    consumer = MagicMock()
    admin_client = MagicMock()
    pkg.KafkaError = _KafkaError  # type: ignore[attr-defined]
    pkg.Consumer = consumer  # type: ignore[attr-defined]
    admin.AdminClient = admin_client  # type: ignore[attr-defined]
    pkg.admin = admin  # type: ignore[attr-defined]
    return {"confluent_kafka": pkg, "confluent_kafka.admin": admin}, consumer, admin_client


def _fake_paho() -> tuple[dict[str, ModuleType], MagicMock]:
    paho = ModuleType("paho")
    mqtt = ModuleType("paho.mqtt")
    client_mod = ModuleType("paho.mqtt.client")
    client_cls = MagicMock()
    client_mod.Client = client_cls  # type: ignore[attr-defined]
    mqtt.client = client_mod  # type: ignore[attr-defined]
    paho.mqtt = mqtt  # type: ignore[attr-defined]
    return {"paho": paho, "paho.mqtt": mqtt, "paho.mqtt.client": client_mod}, client_cls


# ── Kafka ────────────────────────────────────────────────────────────────────

_KAFKA_BAD = [
    {},  # used to default to localhost:9092
    {"bootstrap_servers": "127.0.0.1:9092"},
    {"bootstrap_servers": "SASL_SSL://10.0.0.5:9093"},
    {"bootstrap_servers": "8.8.8.8:9092,169.254.169.254:80"},
    {"bootstrap_servers": "[::1]:9092"},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("cc", _KAFKA_BAD)
async def test_kafka_validate_blocks_internal_bootstrap(cc: dict[str, Any]) -> None:
    from app.ingestion.connectors.kafka_connector import KafkaConnector

    mods, _consumer, admin_client = _fake_kafka()
    with patch.dict(sys.modules, mods):
        health = await KafkaConnector().validate_connection(_config("kafka", cc))
    assert health.ok is False
    admin_client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("cc", _KAFKA_BAD)
async def test_kafka_get_delta_blocks_internal_bootstrap(cc: dict[str, Any]) -> None:
    from app.ingestion.connectors.kafka_connector import KafkaConnector

    mods, consumer, admin_client = _fake_kafka()
    cfg = _config("kafka", {**cc, "topics": ["t"]})
    with patch.dict(sys.modules, mods), pytest.raises(ConnectorEgressBlockedError):
        _ = [d async for d in KafkaConnector().get_delta(cfg, None)]
    consumer.assert_not_called()
    admin_client.assert_not_called()


def _metadata_advertising(host: str) -> MagicMock:
    broker = MagicMock()
    broker.host = host
    broker.port = 9092
    md = MagicMock()
    md.brokers = {1: broker}
    md.topics = {"t": object()}
    return md


@pytest.mark.asyncio
async def test_kafka_validate_blocks_internal_advertised_broker() -> None:
    """A public bootstrap broker that advertises an internal listener is refused."""
    from app.ingestion.connectors.kafka_connector import KafkaConnector

    mods, _consumer, admin_client = _fake_kafka()
    admin_client.return_value.list_topics.return_value = _metadata_advertising("10.1.2.3")
    cfg = _config("kafka", {"bootstrap_servers": "8.8.8.8:9092"})
    with patch.dict(sys.modules, mods):
        health = await KafkaConnector().validate_connection(cfg)
    assert health.ok is False
    assert "10.1.2.3" in (health.error or "")


@pytest.mark.asyncio
async def test_kafka_get_delta_blocks_internal_advertised_broker() -> None:
    from app.ingestion.connectors.kafka_connector import KafkaConnector

    mods, consumer, admin_client = _fake_kafka()
    admin_client.return_value.list_topics.return_value = _metadata_advertising("127.0.0.1")
    cfg = _config("kafka", {"bootstrap_servers": "8.8.8.8:9092", "topics": ["t"]})
    with patch.dict(sys.modules, mods), pytest.raises(ConnectorEgressBlockedError):
        _ = [d async for d in KafkaConnector().get_delta(cfg, None)]
    consumer.assert_not_called()


# ── MQTT ─────────────────────────────────────────────────────────────────────

_MQTT_BAD = [{}, {"host": "127.0.0.1"}, {"host": "169.254.169.254"}, {"host": "10.0.0.5"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("cc", _MQTT_BAD)
async def test_mqtt_validate_blocks_internal_host(cc: dict[str, Any]) -> None:
    from app.ingestion.connectors.mqtt_connector import MQTTConnector

    mods, client_cls = _fake_paho()
    with patch.dict(sys.modules, mods):
        health = await MQTTConnector().validate_connection(_config("mqtt", cc))
    assert health.ok is False
    client_cls.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("cc", _MQTT_BAD)
async def test_mqtt_get_delta_blocks_internal_host(cc: dict[str, Any]) -> None:
    from app.ingestion.connectors.mqtt_connector import MQTTConnector

    mods, client_cls = _fake_paho()
    with patch.dict(sys.modules, mods), pytest.raises(ConnectorEgressBlockedError):
        _ = [d async for d in MQTTConnector().get_delta(_config("mqtt", cc), None)]
    client_cls.assert_not_called()


# ── IMAP ─────────────────────────────────────────────────────────────────────

_IMAP_BAD = [
    {"host": "127.0.0.1", "port": 993},
    {"host": "10.0.0.5", "port": 143, "ssl": False},
    {"host": "169.254.169.254"},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("cc", _IMAP_BAD)
async def test_imap_validate_blocks_internal_host(cc: dict[str, Any]) -> None:
    from app.ingestion.connectors.email_imap_connector import EmailIMAPConnector

    with patch("imaplib.IMAP4_SSL") as ssl_cls, patch("imaplib.IMAP4") as plain_cls:
        health = await EmailIMAPConnector().validate_connection(_config("imap", cc))
    assert health.ok is False
    ssl_cls.assert_not_called()
    plain_cls.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("cc", _IMAP_BAD)
async def test_imap_get_delta_blocks_internal_host(cc: dict[str, Any]) -> None:
    from app.ingestion.connectors.email_imap_connector import EmailIMAPConnector

    with (
        patch("imaplib.IMAP4_SSL") as ssl_cls,
        patch("imaplib.IMAP4") as plain_cls,
        pytest.raises(ConnectorEgressBlockedError),
    ):
        _ = [d async for d in EmailIMAPConnector().get_delta(_config("imap", cc), None)]
    ssl_cls.assert_not_called()
    plain_cls.assert_not_called()
