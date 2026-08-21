"""MQTTConnector — MQTT IoT message ingestion.

Streaming: subscribes to topics and yields messages as RawDocuments.
Cursor: last message timestamp (Unix epoch ms string).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


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
            host = cc.get("host", "localhost")
            port = int(cc.get("port", 1883))

            connected = False

            def on_connect(client, userdata, flags, rc):
                nonlocal connected
                connected = rc == 0

            client = mqtt.Client()
            if cc.get("username"):
                client.username_pw_set(cc["username"], cc.get("password", ""))
            client.on_connect = on_connect
            client.connect_async(host, port, 10)
            client.loop_start()
            import asyncio

            for _ in range(50):  # 5 second timeout
                if connected:
                    break
                await asyncio.sleep(0.1)
            client.loop_stop()
            client.disconnect()
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
        except ImportError:
            _log.error("paho-mqtt not installed")
            return

        cc = config.connection_config
        host = cc.get("host", "localhost")
        port = int(cc.get("port", 1883))
        topics = cc.get("topics") or ["#"]
        max_messages = int(cc.get("max_messages", 1000))
        timeout_seconds = float(cc.get("timeout_seconds", 10.0))

        messages: list[dict] = []
        asyncio.Lock()

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

        client = mqtt.Client()
        if cc.get("username"):
            client.username_pw_set(cc["username"], cc.get("password", ""))
        client.on_message = on_message
        client.connect(host, port, 60)
        for topic in topics:
            client.subscribe(topic, qos=cc.get("qos", 0))
        client.loop_start()
        await asyncio.sleep(timeout_seconds)
        client.loop_stop()
        client.disconnect()

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
                doc_id=str(uuid.uuid4()),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"mqtt://{host}/{msg['topic']}",
                content=text.encode(),
                content_type="application/json" if text.startswith("{") else "text/plain",
                metadata={"topic": msg["topic"], "qos": msg["qos"], "ts_ms": ts},
            )
            yield doc, new_cursor
