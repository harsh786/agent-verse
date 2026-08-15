"""MQTT trigger consumer — subscribes to MQTT topics and fires triggers."""
from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


class MQTTTriggerConsumer:
    """Listen on MQTT topics and dispatch matching triggers."""

    def __init__(
        self,
        *,
        trigger_store: Any = None,
        dispatcher: Any = None,
        mqtt_client: Any = None,
    ) -> None:
        self._store = trigger_store
        self._dispatcher = dispatcher
        self._mqtt = mqtt_client
        self._running = False

    async def start(self) -> None:
        """Connect and subscribe. Requires an async MQTT client (e.g., aiomqtt)."""
        if self._mqtt is None:
            _log.warning("mqtt_consumer_no_client — IoT triggers disabled")
            return
        self._running = True
        _log.info("mqtt_consumer_started")

    async def stop(self) -> None:
        self._running = False

    async def handle_message(
        self,
        topic: str,
        payload_bytes: bytes,
        *,
        tenant_id: str,
        plan: str = "free",
    ) -> None:
        """Process a single MQTT message and fire matching triggers."""
        import json
        try:
            data = json.loads(payload_bytes.decode("utf-8", errors="replace"))
        except Exception:
            data = {"raw": payload_bytes.decode("utf-8", errors="replace")}

        data["mqtt_topic"] = topic

        if self._store is None or self._dispatcher is None:
            return

        triggers = await self._store.find_by_type_async(
            "mqtt", tenant_id=tenant_id
        )
        from types import SimpleNamespace
        tenant_ctx = SimpleNamespace(tenant_id=tenant_id, plan=plan)
        for trigger in triggers:
            spec = trigger.get("spec", trigger)
            # Topic pattern matching
            watch_topic = getattr(spec, "mqtt_topic", "") or trigger.get("mqtt_topic", "")
            if watch_topic and not self._topic_matches(watch_topic, topic):
                continue
            try:
                await self._dispatcher.dispatch(spec, data, tenant_ctx)
            except Exception as exc:
                _log.warning("mqtt_dispatch_error topic=%s: %s", topic, exc)

    def _topic_matches(self, pattern: str, topic: str) -> bool:
        """MQTT wildcard matching: + = single level, # = multi level."""
        pattern_parts = pattern.split("/")
        topic_parts = topic.split("/")
        return self._match_parts(pattern_parts, topic_parts)

    def _match_parts(self, pattern: list, topic: list) -> bool:
        if not pattern and not topic:
            return True
        if not pattern:
            return False
        if pattern[0] == "#":
            return True
        if not topic:
            return False
        if pattern[0] == "+" or pattern[0] == topic[0]:
            return self._match_parts(pattern[1:], topic[1:])
        return False
