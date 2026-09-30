"""Live fan-out of persisted coordination frames to every session subscriber.

The group-chat WebSocket persists each message in the canonical transcript (durable,
replayable by ``after_sequence``) and then publishes it here so every *other*
connection on the session — on this replica or any other — receives it live.

* With Redis (``redis_getter`` returns a client) a frame is published on the
  tenant+session channel and every subscriber, on every replica, reads it from its
  own pub/sub subscription. The publisher's replica receives its own frames through
  Redis too, so nothing is delivered twice.
* Without Redis (single-replica development/tests) frames fan out in process.

Pub/sub is at-most-once: a subscriber that is offline when a frame is published
catches up from the durable transcript on reconnect (``after_sequence`` replay).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_QUEUE_LIMIT = 256


def live_channel(tenant_id: str, session_id: str) -> str:
    """Redis channel carrying one tenant's session frames (tenant id is part of it)."""
    return f"coord:live:{tenant_id}:{session_id}"


class LiveDeliveryUnavailableError(RuntimeError):
    """Redis is configured but a subscription or publish could not be established."""


class CoordinationLiveBus:
    def __init__(self, redis_getter: Callable[[], Any] | None = None) -> None:
        self._redis_getter = redis_getter or (lambda: None)
        self._local: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}

    def _redis(self) -> Any:
        try:
            return self._redis_getter()
        except Exception:  # a broken getter means "no Redis", never a crash
            return None

    async def publish(self, tenant_id: str, session_id: str, frame: dict[str, Any]) -> None:
        channel = live_channel(tenant_id, session_id)
        redis = self._redis()
        if redis is None:
            self._deliver_local(channel, frame)
            return
        try:
            await redis.publish(channel, json.dumps(frame, default=str))
        except Exception as exc:
            raise LiveDeliveryUnavailableError(f"live publish failed: {exc}") from exc

    def _deliver_local(self, channel: str, frame: dict[str, Any]) -> None:
        for queue in tuple(self._local.get(channel, ())):
            if queue.full():
                # A stalled subscriber drops its oldest frame rather than blocking
                # the publisher; it recovers the gap from transcript replay.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(frame)

    @contextlib.asynccontextmanager
    async def subscribe(
        self, tenant_id: str, session_id: str
    ) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        """Subscribe to a session; the subscription is live once this is entered."""
        channel = live_channel(tenant_id, session_id)
        redis = self._redis()
        if redis is None:
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_LIMIT)
            self._local.setdefault(channel, set()).add(queue)
            try:
                yield self._iterate_queue(queue)
            finally:
                subscribers = self._local.get(channel)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._local.pop(channel, None)
            return
        try:
            pubsub = redis.pubsub()
            await pubsub.subscribe(channel)
        except Exception as exc:
            raise LiveDeliveryUnavailableError(f"live subscribe failed: {exc}") from exc
        try:
            yield self._iterate_pubsub(pubsub)
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(channel)
            with contextlib.suppress(Exception):
                await pubsub.aclose()

    @staticmethod
    async def _iterate_queue(
        queue: asyncio.Queue[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        while True:
            yield await queue.get()

    @staticmethod
    async def _iterate_pubsub(pubsub: Any) -> AsyncIterator[dict[str, Any]]:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is None:
                continue
            data = message.get("data")
            if isinstance(data, bytes):
                data = data.decode()
            try:
                frame = json.loads(str(data))
            except ValueError:
                logger.warning("coordination_live_frame_undecodable")
                continue
            if isinstance(frame, dict):
                yield frame


def live_bus_for(state: Any) -> CoordinationLiveBus:
    """The app's bus; an app built without one gets an in-process bus on first use."""
    bus = getattr(state, "coordination_live_bus", None)
    if bus is None:
        bus = CoordinationLiveBus(lambda: getattr(state, "_redis", None))
        state.coordination_live_bus = bus
    return bus  # type: ignore[no-any-return]


__all__ = [
    "CoordinationLiveBus",
    "LiveDeliveryUnavailableError",
    "live_bus_for",
    "live_channel",
]
