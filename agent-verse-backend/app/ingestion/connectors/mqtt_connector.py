"""MQTTConnector — MQTT IoT message ingestion.

Streaming: subscribes to topics and yields messages as RawDocuments.
Cursor: last message timestamp (Unix epoch ms string).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorUnavailableError,
    stable_doc_id,
)
from app.ingestion.connector_egress import ConnectorEgressBlockedError, pin_source_hosts
from app.ingestion.connector_registry import register
from app.ingestion.sdk_executor import run_blocking

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


def _payload_digest(payload: object) -> str:
    """MQTT messages carry no id: the same payload on the same topic is the same item."""
    import hashlib

    return hashlib.sha256(str(payload).encode()).hexdigest()


def _new_client(mqtt: Any) -> Any:
    """A paho-mqtt 2.x client on the VERSION2 callback API.

    paho 2.x refuses ``Client()`` without a callback API version; VERSION2 is the
    current one (``on_connect(client, userdata, flags, reason_code, properties)``).
    """
    return mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)


def _shutdown(client: Any) -> None:
    """Stop paho's network thread and disconnect.

    Both block the caller — ``loop_stop`` joins the network thread (up to its
    select timeout) and ``disconnect`` writes to the socket — so this runs on the
    SDK pool, never on the event loop. Runs even if the sync is cancelled.
    """
    try:
        client.loop_stop()
    finally:
        client.disconnect()


def _broker_host(cc: dict[str, Any]) -> str:
    """Return the tenant broker host (egress-checked and pinned by the caller).

    No ``localhost`` default: an unset host used to make the API/worker dial its
    own loopback on the tenant's behalf.
    """
    host = str(cc.get("host") or "").strip()
    if not host:
        raise ConnectorEgressBlockedError("mqtt: host is required")
    return host


@register("mqtt", feature_flag="ingestion_connector_mqtt_enabled")
class MQTTConnector(BaseConnector):
    """MQTT IoT connector — subscribes to topics via paho-mqtt."""

    source_type = "mqtt"
    supports_streaming = True

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            import paho.mqtt.client as mqtt  # type: ignore[import-not-found]

            cc = config.connection_config
            host = _broker_host(cc)
            port = int(cc.get("port", 1883))

            connected = False

            def on_connect(
                client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None
            ) -> None:
                # paho-mqtt 2.x (VERSION2 callbacks): reason_code is a ReasonCode.
                nonlocal connected
                connected = not reason_code.is_failure

            import asyncio

            # paho resolves the host on its network thread; inside the block that
            # lookup answers with the egress-checked addresses only.
            async with pin_source_hosts([(host, port)], context="mqtt_connector"):
                client = _new_client(mqtt)
                if cc.get("username"):
                    client.username_pw_set(cc["username"], cc.get("password", ""))
                client.on_connect = on_connect
                client.connect_async(host, port, 10)
                client.loop_start()
                try:
                    for _ in range(50):  # 5 second timeout
                        if connected:
                            break
                        await asyncio.sleep(0.1)
                finally:
                    await run_blocking(_shutdown, client)
            latency = (time.perf_counter() - t0) * 1000
            if connected:
                return ConnectionHealth(
                    ok=True, latency_ms=latency, metadata={"host": host, "port": port}
                )
            return ConnectionHealth(ok=False, error="Connection timed out")
        except ImportError:
            return ConnectionHealth(
                ok=False, error="paho-mqtt not installed — pip install paho-mqtt"
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        import asyncio

        from app.ingestion.source_config import RawDocument

        try:
            import paho.mqtt.client as mqtt  # type: ignore[import-not-found]
        except ImportError as exc:
            # Returning nothing here reported a successful, empty sync.
            raise ConnectorUnavailableError(
                "paho-mqtt is not installed on this server; the connector cannot run"
            ) from exc

        cc = config.connection_config
        host = _broker_host(cc)
        port = int(cc.get("port", 1883))
        topics = cc.get("topics") or ["#"]
        max_messages = int(cc.get("max_messages", 1000))
        timeout_seconds = float(cc.get("timeout_seconds", 10.0))

        messages: list[dict] = []

        def on_message(client, userdata, msg):
            payload = msg.payload
            try:
                text = payload.decode("utf-8", errors="replace")
            except Exception:
                text = str(payload)
            messages.append(
                {
                    "topic": msg.topic,
                    "payload": text,
                    "qos": msg.qos,
                }
            )

        async with pin_source_hosts([(host, port)], context="mqtt_connector"):
            client = _new_client(mqtt)
            if cc.get("username"):
                client.username_pw_set(cc["username"], cc.get("password", ""))
            client.on_message = on_message
            await run_blocking(client.connect, host, port, 60)
            try:
                for topic in topics:
                    client.subscribe(topic, qos=cc.get("qos", 0))
                client.loop_start()
                await asyncio.sleep(timeout_seconds)
            finally:
                await run_blocking(_shutdown, client)

        import time as _time

        new_cursor = cursor or ""
        for msg in messages[:max_messages]:
            ts = str(int(_time.time() * 1000))
            new_cursor = ts
            try:
                parsed = json.loads(msg["payload"])
                text = json.dumps(parsed, ensure_ascii=False)
            except Exception:
                text = msg["payload"]
            doc = RawDocument(
                doc_id=stable_doc_id(config, msg["topic"], _payload_digest(msg["payload"])),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"mqtt://{host}/{msg['topic']}",
                content=text.encode(),
                content_type="application/json" if text.startswith("{") else "text/plain",
                metadata={"topic": msg["topic"], "qos": msg["qos"], "ts_ms": ts},
            )
            yield doc, new_cursor
