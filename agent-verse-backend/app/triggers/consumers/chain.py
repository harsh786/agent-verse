"""Goal chain trigger consumer — reads the goal lifecycle trigger stream.

Goal lifecycle events (``goal.completed`` / ``goal.failed`` /
``goal.score_below``) are XADDed to the goal family stream by
``app.triggers.bus.publish_trigger_event``; this consumer reads it with the
``trigger-consumer:chain`` consumer group, so each event is handled once across
replicas and one published while consumers were down is still delivered
(TRG-18).

NOTE: ``hitl.approved`` / ``hitl.rejected`` / ``memory.created`` are deliberately
NOT handled here even though goal-chain triggers can reference those trigger
types. They live on their own streams owned by ``HITLTriggerConsumer`` /
``MemoryTriggerConsumer`` (see ``app/triggers/consumers/hitl.py`` /
``memory.py``), which additionally apply the ``hitl_queue_id`` /
``memory_type`` scoping filters this consumer does not implement. Handling
them here too would dispatch one HITL/memory event twice with two different
idempotency keys (this consumer passes ``source_goal_id`` /
``completion_event_id``, the dedicated consumers do not), so the dispatcher's
dedup would never see a collision and the trigger would fire twice per event.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from app.triggers.bus import run_stream_consumer
from app.triggers.consumers.tenant_ctx import event_tenant_ctx
from app.triggers.lineage import MAX_CHAIN_DEPTH, chained_payload, lineage_from_context

_log = logging.getLogger(__name__)

__all__ = [
    "CHAIN_CHANNEL_FOR_EVENT",
    "MAX_CHAIN_DEPTH",
    "ChainTriggerConsumer",
    "build_chain_event",
    "goal_failed_chain_event",
]

# Goal event type → lifecycle channel this consumer listens on. Published by
# GoalService._dispatch_event (in-process goals) and the run_goal worker.
CHAIN_CHANNEL_FOR_EVENT: dict[str, str] = {
    "goal_complete": "goal.completed",
    "goal_failed": "goal.failed",
    "worker_failed": "goal.failed",
}


def build_chain_event(
    *,
    channel: str,
    tenant_id: str,
    goal_id: str,
    agent_id: str = "",
    status: str = "",
    tenant_plan: str = "free",
    trigger_chain_depth: int = 0,
    score: float | None = None,
    source_trigger_id: str = "",
    scores: dict[str, float] | None = None,
) -> str:
    """JSON payload for a goal lifecycle channel.

    ``completion_event_id`` is deterministic (goal + channel) so the same terminal
    event relayed twice maps to one dispatcher idempotency key.
    ``source_trigger_id`` is the goal-event trigger that created this goal (if
    any); the consumer never re-fires that trigger on it.
    """
    payload: dict[str, object] = {
        "tenant_id": tenant_id,
        "goal_id": goal_id,
        "agent_id": agent_id or "",
        "status": status,
        "tenant_plan": tenant_plan or "free",
        "trigger_chain_depth": int(trigger_chain_depth or 0),
        "completion_event_id": f"{goal_id}:{channel}",
    }
    if score is not None:
        payload["score"] = score
    if scores:
        # Per-dimension scores, for triggers that watch one dimension (B7-5).
        numeric = {str(k): _as_float(v) for k, v in scores.items()}
        payload["scores"] = {k: v for k, v in numeric.items() if v is not None}
    if source_trigger_id:
        payload["source_trigger_id"] = source_trigger_id
    return json.dumps(payload)


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _observed_score(spec: object, data: dict) -> float | None:
    """The score a goal_score_below trigger compares: its ``score_dimension``
    from the event's per-dimension scores, else (blank / ``overall``) the
    overall average. ``None`` when the event does not carry it."""
    dimension = str(getattr(spec, "score_dimension", "") or "").strip()
    if dimension and dimension.lower() != "overall":
        scores = data.get("scores")
        return _as_float(scores.get(dimension)) if isinstance(scores, dict) else None
    return _as_float(data.get("score"))


def goal_failed_chain_event(
    *, tenant_id: str, goal_id: str, agent_id: str = "", execution_context: object = None
) -> str:
    """``goal.failed`` payload for a goal failed outside its runner (B7-2).

    The stuck-goal detector, the stale-runner watchdog, the HITL-expiry sweep and
    the DLQ fail goals by a DB update; this builds the same event the runner
    would publish, with the goal's trigger lineage from its execution_context
    (so the loop guard holds) and the deterministic completion id (so a goal
    whose runner also published ``goal.failed`` still fires each trigger once).
    """
    lineage = lineage_from_context(execution_context)
    return build_chain_event(
        channel="goal.failed",
        tenant_id=tenant_id,
        goal_id=goal_id,
        agent_id=agent_id or "",
        status="failed",
        trigger_chain_depth=lineage.depth,
        source_trigger_id=lineage.source_trigger_id,
    )


class ChainTriggerConsumer:
    """Reads goal lifecycle events from the trigger stream and fires chain triggers."""

    CHANNELS: list[str] = [  # noqa: RUF012
        "goal.completed",
        "goal.failed",
        "goal.score_below",
    ]
    # Consumer group on the goal stream (one delivery per event across replicas).
    GROUP = "trigger-consumer:chain"

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
        """Consume the goal lifecycle stream (consumer group ``GROUP``)."""
        if self._redis is None:
            _log.warning("chain_consumer_no_redis — goal chain triggers disabled")
            return
        self._running = True
        try:
            await run_stream_consumer(
                self, label="chain_consumer", channel=self.CHANNELS[0], group=self.GROUP
            )
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
        if not isinstance(data, dict):
            return

        # The chain depth cap (MAX_CHAIN_DEPTH) is enforced AND audited by the
        # dispatcher's loop guard, per matching trigger (B7-L3): returning here
        # stopped the chain with a log line only, no trigger_events row.
        chain_depth = lineage_from_context(data).depth

        trigger_type = self._channel_to_type(channel)
        if trigger_type is None:
            return

        await self._dispatch_matching(trigger_type, data, chain_depth)

    def _channel_to_type(self, channel: str) -> str | None:
        return {
            "goal.completed": "goal_completed",
            "goal.failed": "goal_failed",
            "goal.score_below": "goal_score_below",
        }.get(channel)

    async def _dispatch_matching(self, trigger_type: str, data: dict, chain_depth: int) -> None:
        if self._store is None or self._dispatcher is None:
            return

        tenant_id = data.get("tenant_id", "")
        goal_id = data.get("goal_id", "")
        agent_id = data.get("agent_id", "")

        # Find all enabled triggers of this type for this tenant
        try:
            triggers = await self._store.find_by_type_async(
                trigger_type=trigger_type,
                tenant_id=tenant_id,
                strict=True,
            )
        except Exception as exc:
            # Not accepted: raise so the stream entry stays pending and is
            # retried, instead of acking an event whose triggers were never seen.
            _log.warning("chain_store_error: %s", exc)
            raise

        tenant_ctx: SimpleNamespace | None = None
        for trigger in triggers:
            # ``find_by_type_async`` returns plain dict records (the trigger's
            # TriggerSpec lives under the "spec" key) — ``getattr(trigger, "spec",
            # trigger)`` always fell through to the default (a dict does not expose
            # its keys as attributes), so ``spec`` silently ended up being the raw
            # dict on every call. ``dispatcher.dispatch()`` then raised
            # ``AttributeError: 'dict' object has no attribute 'trigger_type'``,
            # caught by the except-block below and merely logged — so this
            # consumer never actually fired a single goal_completed / goal_failed
            # / goal_score_below trigger. ``.get("spec", trigger)`` (matching
            # HITLTriggerConsumer / MemoryTriggerConsumer) fixes the extraction.
            spec = trigger.get("spec", trigger) if isinstance(trigger, dict) else trigger

            # The self-chain guard (a trigger never fires on a goal it created,
            # unless allow_self_trigger) runs in the dispatcher, which audits it.

            # Filter by watch_agent_id / watch_goal_id if set. watch_agent_id is
            # the SOURCE filter (whose goals to watch); the agent the trigger
            # runs is the spec's separate agent_id.
            if getattr(spec, "watch_agent_id", "") and spec.watch_agent_id != agent_id:
                continue
            if getattr(spec, "watch_goal_id", "") and spec.watch_goal_id != goal_id:
                continue

            # GOAL_SCORE_BELOW: the watched score (one dimension, or the overall
            # average) must be below the threshold; no score, no fire (B7-5).
            if trigger_type == "goal_score_below":
                observed = _observed_score(spec, data)
                threshold = _as_float(getattr(spec, "score_threshold", None))
                if observed is None or threshold is None or observed >= threshold:
                    continue

            # Plan from the tenant record — never from the event payload.
            if tenant_ctx is None:
                tenant_ctx = await event_tenant_ctx(self._dispatcher, tenant_id)

            enriched = chained_payload(data, lineage_from_context(data))
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
