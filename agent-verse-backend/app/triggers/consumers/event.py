"""Generic EVENT trigger consumer (2.W-1).

Fires ``EVENT`` triggers when a matching custom event is published to Redis.

Convention: events are published — server-side, from an authenticated path that
stamps the caller's ``tenant_id`` — to the channel
``trigger:event:{event_channel}`` with a JSON payload carrying at least
``tenant_id``. A single ``psubscribe`` on ``trigger:event:*`` catches every EVENT
channel, so newly-created triggers fire without re-subscription. Firing is
tenant-scoped: only EVENT triggers belonging to the payload's ``tenant_id`` and
whose ``event_channel`` matches are dispatched, so one tenant cannot fire
another's trigger.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

_log = logging.getLogger(__name__)

CHANNEL_PREFIX = "trigger:event:"
CHANNEL_PATTERN = "trigger:event:*"


def event_channel_name(event_channel: str) -> str:
    """The Redis channel an EVENT trigger's ``event_channel`` maps to."""
    return f"{CHANNEL_PREFIX}{event_channel}"


async def publish_trigger_event(
    redis: Any, *, event_channel: str, tenant_id: str, payload: dict[str, Any] | None = None
) -> None:
    """Publish a custom event that EVENT triggers can fire on. Callers must pass
    the authenticated ``tenant_id`` (never a client-supplied one)."""
    body = {**(payload or {}), "tenant_id": tenant_id, "event_channel": event_channel}
    result = redis.publish(event_channel_name(event_channel), json.dumps(body))
    if hasattr(result, "__await__"):
        await result


def _decode(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value or "")


class EventTriggerConsumer:
    """Listens on Redis pub/sub for custom events and fires EVENT triggers."""

    def __init__(
        self,
        *,
        trigger_store: object | None = None,
        dispatcher: object | None = None,
        redis: object | None = None,
    ) -> None:
        self._store = trigger_store
        self._dispatcher = dispatcher
        self._redis = redis
        self._running = False

    async def start(self) -> None:
        if self._redis is None:
            _log.warning("event_consumer_no_redis — EVENT triggers disabled")
            return
        self._running = True
        try:
            pubsub = self._redis.pubsub()  # type: ignore[attr-defined]
            await pubsub.psubscribe(CHANNEL_PATTERN)
            _log.info("event_consumer_started pattern=%s", CHANNEL_PATTERN)
            async for message in pubsub.listen():
                if not self._running:
                    break
                if message.get("type") != "pmessage":
                    continue
                await self._handle(message)
        except Exception as exc:  # pragma: no cover - defensive
            _log.error("event_consumer_error: %s", exc)

    async def stop(self) -> None:
        self._running = False

    async def _handle(self, message: dict) -> None:
        channel = _decode(message.get("channel"))
        if not channel.startswith(CHANNEL_PREFIX):
            return
        event_channel = channel[len(CHANNEL_PREFIX) :]
        try:
            data = json.loads(_decode(message.get("data")))
        except Exception:
            return
        if isinstance(data, dict):
            await self._dispatch_matching(event_channel, data)

    async def _dispatch_matching(self, event_channel: str, data: dict) -> None:
        if self._store is None or self._dispatcher is None:
            return
        tenant_id = data.get("tenant_id", "")
        if not tenant_id:
            return  # unscoped events are never dispatched (no cross-tenant fire)
        try:
            triggers = await self._store.find_by_type_async(  # type: ignore[attr-defined]
                "event", tenant_id=tenant_id
            )
        except Exception as exc:
            _log.warning("event_store_error: %s", exc)
            return

        for trigger in triggers:
            spec = (
                trigger.get("spec") if isinstance(trigger, dict) else getattr(trigger, "spec", None)
            )
            if spec is None:
                continue
            if getattr(spec, "event_channel", "") != event_channel:
                continue
            tenant_ctx = SimpleNamespace(tenant_id=tenant_id, plan=data.get("tenant_plan", "free"))
            try:
                await self._dispatcher.dispatch(  # type: ignore[attr-defined]
                    spec, data, tenant_ctx, message_id=str(data.get("event_id", "") or "")
                )
            except Exception as exc:
                _log.warning("event_dispatch_error: %s", exc)
