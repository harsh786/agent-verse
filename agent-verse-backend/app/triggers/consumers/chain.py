"""Goal chain trigger consumer — subscribes to Redis goal lifecycle events."""

from __future__ import annotations

import json
import logging

_log = logging.getLogger(__name__)

MAX_CHAIN_DEPTH = 10


class ChainTriggerConsumer:
    """Listens on Redis pub/sub for goal lifecycle events and fires chain triggers."""

    CHANNELS: list[str] = [  # noqa: RUF012
        "goal.completed",
        "goal.failed",
        "goal.score_below",
        "hitl.approved",
        "hitl.rejected",
        "memory.created",
    ]

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
        """Subscribe to all goal lifecycle channels and process messages."""
        if self._redis is None:
            _log.warning("chain_consumer_no_redis — goal chain triggers disabled")
            return
        self._running = True
        try:
            pubsub = self._redis.pubsub()
            await pubsub.subscribe(*self.CHANNELS)
            _log.info("chain_consumer_started channels=%s", self.CHANNELS)
            async for message in pubsub.listen():
                if not self._running:
                    break
                if message.get("type") != "message":
                    continue
                await self._handle(message)
        except Exception as exc:
            _log.error("chain_consumer_error: %s", exc)

    async def stop(self) -> None:
        self._running = False

    async def _handle(self, message: dict) -> None:
        channel = (
            message.get("channel", b"").decode()
            if isinstance(message.get("channel"), bytes)
            else message.get("channel", "")
        )
        raw = message.get("data", b"")
        try:
            data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        except Exception:
            return

        # Enforce chain depth limit
        chain_depth = data.get("trigger_chain_depth", 0)
        if chain_depth >= MAX_CHAIN_DEPTH:
            _log.warning(
                "chain_depth_exceeded depth=%d goal_id=%s",
                chain_depth,
                data.get("goal_id"),
            )
            return

        trigger_type = self._channel_to_type(channel)
        if trigger_type is None:
            return

        await self._dispatch_matching(trigger_type, data, chain_depth)

    def _channel_to_type(self, channel: str) -> str | None:
        return {
            "goal.completed": "goal_completed",
            "goal.failed": "goal_failed",
            "goal.score_below": "goal_score_below",
            "hitl.approved": "hitl_approved",
            "hitl.rejected": "hitl_rejected",
            "memory.created": "memory_created",
        }.get(channel)

    async def _dispatch_matching(self, trigger_type: str, data: dict, chain_depth: int) -> None:
        if self._store is None or self._dispatcher is None:
            return

        tenant_id = data.get("tenant_id", "")
        goal_id = data.get("goal_id", "")
        agent_id = data.get("agent_id", "")
        score = data.get("score", 1.0)

        # Find all enabled triggers of this type for this tenant
        try:
            triggers = await self._store.find_by_type_async(
                trigger_type=trigger_type,
                tenant_id=tenant_id,
            )
        except Exception as exc:
            _log.warning("chain_store_error: %s", exc)
            return

        for trigger in triggers:
            spec = getattr(trigger, "spec", trigger)

            # Filter by watch_agent_id / watch_goal_id if set
            if getattr(spec, "watch_agent_id", "") and spec.watch_agent_id != agent_id:
                continue
            if getattr(spec, "watch_goal_id", "") and spec.watch_goal_id != goal_id:
                continue

            # Filter by score threshold for GOAL_SCORE_BELOW
            if trigger_type == "goal_score_below":
                threshold = getattr(spec, "score_threshold", 0.8)
                if score >= threshold:
                    continue

            # Build a simple tenant context from the event data
            from types import SimpleNamespace

            tenant_ctx = SimpleNamespace(
                tenant_id=tenant_id,
                plan=data.get("tenant_plan", "free"),
            )

            enriched = {**data, "trigger_chain_depth": chain_depth + 1}
            try:
                await self._dispatcher.dispatch(
                    spec,
                    enriched,
                    tenant_ctx,
                    source_goal_id=goal_id,
                    completion_event_id=data.get("completion_event_id", ""),
                )
            except Exception as exc:
                _log.warning(
                    "chain_dispatch_error trigger_id=%s: %s",
                    getattr(trigger, "trigger_id", "?"),
                    exc,
                )
