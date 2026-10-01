"""Generic EVENT trigger consumer (2.W-1).

Fires ``EVENT`` triggers when a matching custom event is published to Redis.

Convention: events are published — server-side, from an authenticated path that
stamps the caller's ``tenant_id`` — to the channel
``trigger:event:{event_channel}`` with a JSON payload carrying at least
``tenant_id``. Every ``trigger:event:*`` channel is XADDed to the one EVENT-family
stream (``app.triggers.bus``, TRG-18), which this consumer reads with its own
consumer group, so newly-created triggers fire without re-subscription and an
event published while consumers are down is delivered later. Firing is
tenant-scoped: only EVENT triggers belonging to the payload's ``tenant_id`` and
whose ``event_channel`` matches are dispatched, so one tenant cannot fire
another's trigger.
"""

from __future__ import annotations

import json
import logging
import uuid
from types import SimpleNamespace
from typing import Any

from app.triggers.bus import publish_trigger_event as publish_bus_event
from app.triggers.bus import run_stream_consumer
from app.triggers.consumers.tenant_ctx import event_tenant_ctx, strip_reserved

_log = logging.getLogger(__name__)

CHANNEL_PREFIX = "trigger:event:"
CHANNEL_PATTERN = "trigger:event:*"
# Any ``trigger:event:*`` channel: resolves the EVENT-family stream.
CONVENTION_CHANNEL = f"{CHANNEL_PREFIX}any"


def event_channel_name(event_channel: str) -> str:
    """The Redis channel an EVENT trigger's ``event_channel`` maps to."""
    return f"{CHANNEL_PREFIX}{event_channel}"


async def publish_trigger_event(
    redis: Any, *, event_channel: str, tenant_id: str, payload: dict[str, Any] | None = None
) -> None:
    """Publish a custom event that EVENT triggers can fire on. Callers must pass
    the authenticated ``tenant_id`` (never a client-supplied one). Reserved keys
    in the client payload (``tenant_id`` / ``tenant_plan`` / ``event_channel``)
    are dropped: consumers resolve the plan from the tenant record."""
    body = {**strip_reserved(payload), "tenant_id": tenant_id, "event_channel": event_channel}
    # Every event gets an id (the client's, else a fresh one): replicas key
    # shared condition state and dispatcher idempotency on it, and two
    # identical payloads are still two events (TRG-19).
    if not str(body.get("event_id", "") or ""):
        body["event_id"] = uuid.uuid4().hex
    # Stream XADD (+ legacy pub/sub while dual publish is on), TRG-18.
    await publish_bus_event(redis, event_channel_name(event_channel), body)


def _decode(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value or "")


class EventTriggerConsumer:
    """Reads custom events from the EVENT trigger stream and fires EVENT triggers."""

    # Consumer group on the EVENT-family stream (TRG-18).
    GROUP = "trigger-consumer:event"

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
            await run_stream_consumer(
                self, label="event_consumer", channel=CONVENTION_CHANNEL, group=self.GROUP
            )
        except Exception as exc:
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
                "event", tenant_id=tenant_id, strict=True
            )
        except Exception as exc:
            # Not accepted: the stream entry stays pending and is retried.
            _log.warning("event_store_error: %s", exc)
            raise

        tenant_ctx: SimpleNamespace | None = None
        for trigger in triggers:
            spec = (
                trigger.get("spec") if isinstance(trigger, dict) else getattr(trigger, "spec", None)
            )
            if spec is None:
                continue
            if getattr(spec, "event_channel", "") != event_channel:
                continue
            if tenant_ctx is None:  # plan from the tenant record, never the event
                tenant_ctx = await event_tenant_ctx(self._dispatcher, tenant_id)
            try:
                await self._dispatcher.dispatch(  # type: ignore[attr-defined]
                    spec, data, tenant_ctx, message_id=str(data.get("event_id", "") or "")
                )
            except Exception as exc:
                _log.warning("event_dispatch_error: %s", exc)
