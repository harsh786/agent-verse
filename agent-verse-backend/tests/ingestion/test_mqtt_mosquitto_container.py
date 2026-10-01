"""UNPIN-SDKS: the MQTT connector on paho-mqtt 2.x against a real broker.

The connector was written for paho-mqtt 1.x (``mqtt.Client()``,
``on_connect(client, userdata, flags, rc)``); paho 2.x refuses ``Client()``
without a callback API version, so the package was pinned below 2. It now uses
the VERSION2 callback API. This runs validate and a sync against eclipse-mosquitto
(testcontainers): a retained message published beforehand must be ingested.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest

from app.ingestion.source_config import SourceConfig, SourceFamily

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def broker_port() -> Iterator[int]:
    import re

    import paho.mqtt.client as mqtt
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy

    container = (
        DockerContainer("eclipse-mosquitto:2")
        .with_command("mosquitto -c /mosquitto-no-auth.conf")
        .with_exposed_ports(1883)
        .waiting_for(LogMessageWaitStrategy(re.compile(r"mosquitto version .* running")))
    )
    with container:
        port = int(container.get_exposed_port(1883))
        publisher = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        for _ in range(40):
            try:
                publisher.connect("localhost", port, 10)
                break
            except OSError:
                time.sleep(0.25)
        publisher.loop_start()
        info = publisher.publish("sensors/temp", b'{"temp": 21.5}', qos=1, retain=True)
        info.wait_for_publish(timeout=10)
        publisher.loop_stop()
        publisher.disconnect()
        yield port


@pytest.fixture(autouse=True)
def _allow_localhost(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "localhost")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _config(port: int, **cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-mqtt",
        tenant_id="tenant-mqtt",
        name="mqtt",
        family=SourceFamily.WEB,
        source_type="mqtt",
        collection_id="col-1",
        connection_config={"host": "localhost", "port": port, **cc},
    )


async def test_validate_connection_on_paho_2(broker_port: int) -> None:
    from app.ingestion.connectors.mqtt_connector import MQTTConnector

    health = await MQTTConnector().validate_connection(_config(broker_port))
    assert health.ok, health.error


async def test_sync_ingests_a_retained_message_on_paho_2(broker_port: int) -> None:
    from app.ingestion.connectors.mqtt_connector import MQTTConnector

    cfg = _config(broker_port, topics=["sensors/#"], timeout_seconds=2)
    docs = [d async for d, _c in MQTTConnector().get_delta(cfg, None)]
    assert len(docs) == 1
    assert b"21.5" in docs[0].content
    assert docs[0].metadata.get("topic") == "sensors/temp"
