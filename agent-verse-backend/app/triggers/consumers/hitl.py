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

        if not isinstance(data, dict):
            return
        trigger_type = "hitl_approved" if "approved" in channel else "hitl_rejected"
        tenant_id = data.get("tenant_id", "")
        if not tenant_id or self._store is None or self._dispatcher is None:
            return

        triggers = await self._store.find_by_type_async(
            trigger_type, tenant_id=tenant_id, strict=True
        )
        if not triggers:
            return
        from app.governance.hitl_queues import matches
        from app.triggers.consumers.tenant_ctx import event_tenant_ctx
        from app.triggers.lineage import MAX_CHAIN_DEPTH, chained_payload, source_lineage

        # B7 loop guard: the lineage of the goal whose approval this is. A read
        # error raises, so the stream entry is retried, never fired unchecked.
        goal_id = str(data.get("goal_id", "") or "")
        lineage = await source_lineage(self._dispatcher, tenant_id, goal_id, data)
        if lineage.depth >= MAX_CHAIN_DEPTH:
            _log.warning("hitl_chain_depth_exceeded depth=%d goal_id=%s", lineage.depth, goal_id)
            return
        payload = chained_payload(data, lineage)
        # One decision is one firing, whichever replica relays it and whatever
        # else (approver note, plan stamp) differs between the copies.
        request_id = str(data.get("request_id", "") or "")
        event_id = f"{request_id}:{channel}" if request_id else ""

        # Plan from the tenant record — never from the event payload.
        tenant_ctx = await event_tenant_ctx(self._dispatcher, tenant_id)
        for trigger in triggers:
            spec = trigger.get("spec", trigger)
            # TRG-23: the filter matches either derived queue (agent:<id> / risk:<tier>).
            if not matches(getattr(spec, "hitl_queue_id", "") or "", data):
                continue
            try:
                await self._dispatcher.dispatch(
                    spec,
                    payload,
                    tenant_ctx,
                    **(
                        {"source_goal_id": goal_id, "completion_event_id": event_id}
                        if event_id
                        else {}
                    ),
                )
            except Exception as exc:
                _log.warning("hitl_dispatch_error: %s", exc)
