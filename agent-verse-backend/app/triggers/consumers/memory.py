"""Memory creation trigger consumer."""
from __future__ import annotations

import json
import logging
from typing import Any

_log = logging.getLogger(__name__)


class MemoryTriggerConsumer:
    """Subscribe to memory creation events and dispatch matching triggers."""

    CHANNELS: list[str] = ["memory.created"]  # noqa: RUF012

    def __init__(
        self,
        *,
        trigger_store: Any = None,
        dispatcher: Any = None,
        redis: Any = None,
    ) -> None:
        self._store = trigger_store
        self._dispatcher = dispatcher
        self._redis = redis
        self._running = False

    async def start(self) -> None:
        if self._redis is None:
            _log.warning("memory_consumer_no_redis — memory triggers disabled")
            return
        self._running = True
        try:
            pubsub = self._redis.pubsub()
            await pubsub.subscribe(*self.CHANNELS)
            _log.info("memory_consumer_started")
            async for message in pubsub.listen():
                if not self._running:
                    break
                if message.get("type") != "message":
                    continue
                await self._handle(message)
        except Exception as exc:
            _log.error("memory_consumer_error: %s", exc)

    async def stop(self) -> None:
        self._running = False

    async def _handle(self, message: dict) -> None:
        raw = message.get("data", b"")
        try:
            data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        except Exception:
            return

        tenant_id = data.get("tenant_id", "")
        if not tenant_id or self._store is None or self._dispatcher is None:
            return

        triggers = await self._store.find_by_type_async("memory_created", tenant_id=tenant_id)
        from types import SimpleNamespace
        tenant_ctx = SimpleNamespace(
            tenant_id=tenant_id,
            plan=data.get("tenant_plan", "free"),
        )
        for trigger in triggers:
            spec = trigger.get("spec", trigger)
            # Filter by memory_type if specified
            watch_type = getattr(spec, "memory_type", "") or ""
            if watch_type and watch_type != data.get("memory_type", ""):
                continue
            try:
                await self._dispatcher.dispatch(spec, data, tenant_ctx)
            except Exception as exc:
                _log.warning("memory_dispatch_error: %s", exc)
