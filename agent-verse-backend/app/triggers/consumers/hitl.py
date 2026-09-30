"""HITL (Human-in-the-Loop) trigger consumer — reads the HITL trigger stream (TRG-18)."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.triggers.bus import run_stream_consumer

_log = logging.getLogger(__name__)


class HITLTriggerConsumer:
    """Consume HITL approval/rejection events and dispatch matching triggers."""

    CHANNELS: list[str] = ["hitl.approved", "hitl.rejected"]  # noqa: RUF012
    # Consumer group on the HITL trigger stream (TRG-18).
    GROUP = "trigger-consumer:hitl"

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
            _log.warning("hitl_consumer_no_redis — HITL triggers disabled")
            return
        self._running = True
        try:
            await run_stream_consumer(
                self, label="hitl_consumer", channel=self.CHANNELS[0], group=self.GROUP
            )
        except Exception as exc:
            _log.error("hitl_consumer_error: %s", exc)

    async def stop(self) -> None:
        self._running = False

    async def _handle(self, message: dict) -> None:
        channel = message.get("channel", b"")
        if isinstance(channel, bytes):
            channel = channel.decode()
        raw = message.get("data", b"")
        try:
            data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        except Exception:
            return

        trigger_type = "hitl_approved" if "approved" in channel else "hitl_rejected"
        tenant_id = data.get("tenant_id", "")
        if not tenant_id or self._store is None or self._dispatcher is None:
            return

        triggers = await self._store.find_by_type_async(trigger_type, tenant_id=tenant_id)
        if not triggers:
            return
        from app.triggers.consumers.tenant_ctx import event_tenant_ctx

        # Plan from the tenant record — never from the event payload.
        tenant_ctx = await event_tenant_ctx(self._dispatcher, tenant_id)
        for trigger in triggers:
            spec = trigger.get("spec", trigger)
            queue_id = data.get("hitl_queue_id", "")
            watch_queue = getattr(spec, "hitl_queue_id", "") or ""
            if watch_queue and watch_queue != queue_id:
                continue
            try:
                await self._dispatcher.dispatch(spec, data, tenant_ctx)
            except Exception as exc:
                _log.warning("hitl_dispatch_error: %s", exc)
