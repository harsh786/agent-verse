"""Condition/state trigger consumer — Family D (2.W-1).

Semantic ruling: Family D triggers are *event-driven evaluators* on the EVENT
bus. This consumer subscribes to the same ``trigger:event:*`` stream and, on each
event, evaluates the tenant's condition/counter/compound/state/window triggers
against the payload — wiring the previously-orphaned evaluators in
``app.triggers.condition.evaluator`` onto the live path:

  * CONDITION        — fire when the CEL condition holds against the payload.
  * COUNTER_THRESHOLD— count matching events in a sliding window; fire at threshold.
  * WINDOW_AGGREGATE — aggregate a numeric field over a window; fire past threshold.
  * STATE_TRANSITION — track the payload's ``state``; fire on from→to transition.
  * COMPOUND         — AND/OR/NOT over referenced triggers' conditions.

Evaluator state is per-consumer in-memory (single worker); the docstrings in the
evaluator module note Redis-backed variants for multi-worker scale. Firing is
tenant-scoped via ``find_by_type_async`` and dispatched through the governed
TriggerDispatcher.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import time
from typing import Any

from app.triggers.bus import run_stream_consumer
from app.triggers.condition.evaluator import (
    CELEvaluator,
    CompoundTriggerEvaluator,
    CounterThresholdEvaluator,
    WindowAggregateEvaluator,
)
from app.triggers.consumers.event import CHANNEL_PREFIX, CONVENTION_CHANNEL, _decode
from app.triggers.consumers.tenant_ctx import event_tenant_ctx
from app.triggers.polling import extract_path

_log = logging.getLogger(__name__)

_FAMILY_TYPES = (
    "condition",
    "counter_threshold",
    "compound",
    "state_transition",
    "window_aggregate",
)


# Family D types whose evaluation depends on earlier events (shared in Redis).
_STATEFUL_TYPES = frozenset({"counter_threshold", "window_aggregate", "state_transition"})


class ConditionTriggerConsumer:
    """Evaluates Family D (condition/state) triggers on the EVENT bus."""

    # Consumer group on the EVENT-family stream (TRG-18).
    GROUP = "trigger-consumer:condition"

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
        self._cel = CELEvaluator()
        self._counter = CounterThresholdEvaluator()
        self._window = WindowAggregateEvaluator()
        self._compound = CompoundTriggerEvaluator()
        self._last_state: dict[str, str] = {}

    async def start(self) -> None:
        if self._redis is None:
            _log.warning("condition_consumer_no_redis — Family D triggers disabled")
            return
        self._running = True
        try:
            await run_stream_consumer(
                self, label="condition_consumer", channel=CONVENTION_CHANNEL, group=self.GROUP
            )
        except Exception as exc:
            _log.error("condition_consumer_error: %s", exc)

    async def stop(self) -> None:
        self._running = False

    async def _handle(self, message: dict) -> None:
        channel = _decode(message.get("channel"))
        if not channel.startswith(CHANNEL_PREFIX):
            return
        try:
            data = json.loads(_decode(message.get("data")))
        except Exception:
            return
        if isinstance(data, dict):
            await self._dispatch_matching(data)

    async def _dispatch_matching(self, payload: dict) -> None:
        if self._store is None or self._dispatcher is None:
            return
        tenant_id = payload.get("tenant_id", "")
        if not tenant_id:
            return

        # Load every Family D trigger for this tenant once, indexed by id.
        by_type: dict[str, list[Any]] = {}
        index: dict[str, Any] = {}
        store_error: Exception | None = None
        for ttype in _FAMILY_TYPES:
            try:
                triggers = await self._store.find_by_type_async(  # type: ignore[attr-defined]
                    ttype, tenant_id=tenant_id, strict=True
                )
            except Exception as exc:
                _log.warning("condition_store_error type=%s: %s", ttype, exc)
                store_error = exc
                continue
            by_type[ttype] = triggers
            for trig in triggers:
                spec = _spec_of(trig)
                if spec is not None:
                    index[str(getattr(spec, "trigger_id", "") or id(trig))] = spec

        for ttype, triggers in by_type.items():
            for trig in triggers:
                spec = _spec_of(trig)
                if spec is None:
                    continue
                trigger_id = str(getattr(spec, "trigger_id", "") or id(trig))
                try:
                    if await self._should_fire_async(
                        ttype, spec, trigger_id, payload, index, tenant_id
                    ):
                        await self._dispatch(spec, payload, tenant_id)
                except Exception as exc:
                    _log.warning("condition_eval_error type=%s: %s", ttype, exc)
        if store_error is not None:
            # Some triggers were never looked up: not accepted, so the stream
            # entry stays pending and is retried (claims + dedup keep the
            # already-evaluated triggers from firing twice) — TRG-18.
            raise store_error

    # ── Shared (Redis) evaluator state — TRG-19 ───────────────────────────────
    # Every replica used to receive every event (pub/sub fan-out; consumer groups
    # now deliver once per group, but a reclaimed entry is redelivered) and kept
    # counters / windows / last states in its own memory, so thresholds fired
    # once per replica at different events, or never after a restart. With Redis
    # the state is keyed by tenant + trigger, and each (trigger, event) pair is
    # CLAIMED by exactly one replica (SET NX on the event id), which then applies
    # it — so N replicas count every event once.

    _STATE_TTL_S = 7 * 24 * 3600

    def _stateful_redis(self) -> Any:
        """The Redis to keep evaluator state in, or None (in-memory fallback)."""
        redis = self._redis
        return redis if redis is not None and hasattr(redis, "pipeline") else None

    @staticmethod
    def _event_member(payload: dict) -> str:
        event_id = str(payload.get("event_id", "") or "")
        if event_id:
            return event_id
        return (
            "h:"
            + hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        )

    async def _claim(self, redis: Any, tenant_id: str, trigger_id: str, member: str) -> bool:
        key = f"trigger:cond:seen:{tenant_id}:{trigger_id}:{member}"
        return bool(await redis.set(key, "1", nx=True, ex=self._STATE_TTL_S))

    async def _should_fire_async(
        self,
        ttype: str,
        spec: Any,
        trigger_id: str,
        payload: dict,
        index: dict[str, Any],
        tenant_id: str,
    ) -> bool:
        redis = self._stateful_redis()
        if redis is None or ttype not in _STATEFUL_TYPES:
            return self._should_fire(ttype, spec, trigger_id, payload, index)
        member = self._event_member(payload)
        if ttype == "counter_threshold":
            threshold = int(getattr(spec, "counter_threshold", 0) or 0)
            if threshold <= 0 or not await self._claim(redis, tenant_id, trigger_id, member):
                return False
            window = int(getattr(spec, "counter_window_secs", 3600) or 3600)
            key = (
                f"trigger:cond:counter:{tenant_id}:{trigger_id}:"
                f"{getattr(spec, 'counter_key', '') or 'default'}"
            )
            now = time.time()
            async with redis.pipeline(transaction=True) as pipe:
                pipe.zadd(key, {member: now}, nx=True)
                pipe.zremrangebyscore(key, "-inf", now - window)
                pipe.zcard(key)
                pipe.expire(key, window)
                _, _, count, _ = await pipe.execute()
            # Exactly one event can take the count to the threshold (the add and
            # the count are atomic); it fires and removes the batch it completed.
            if int(count) == threshold:
                await redis.zremrangebyrank(key, 0, threshold - 1)
                return True
            return False
        if ttype == "window_aggregate":
            field = getattr(spec, "window_field", "") or ""
            raw = extract_path(payload, field) if field else None
            try:
                value = float(raw)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return False
            if not await self._claim(redis, tenant_id, trigger_id, member):
                return False
            window = int(getattr(spec, "window_seconds", 300) or 300)
            key = f"trigger:cond:window:{tenant_id}:{trigger_id}"
            now = time.time()
            async with redis.pipeline(transaction=True) as pipe:
                pipe.zadd(key, {f"{member}|{value!r}": now}, nx=True)
                pipe.zremrangebyscore(key, "-inf", now - window)
                pipe.zrange(key, 0, -1)
                pipe.expire(key, window)
                _, _, members, _ = await pipe.execute()
            values = []
            for item in members:
                text = item.decode() if isinstance(item, bytes) else str(item)
                with contextlib.suppress(ValueError):
                    values.append(float(text.rsplit("|", 1)[1]))
            if not values:
                return False
            agg = WindowAggregateEvaluator.AGGREGATIONS.get(
                getattr(spec, "window_aggregation", "sum") or "sum",
                WindowAggregateEvaluator.AGGREGATIONS["avg"],
            )
            return bool(agg(values) > float(getattr(spec, "window_threshold", 0.0) or 0.0))  # type: ignore[operator]
        # state_transition
        current = str(payload.get("state", "") or "")
        if not current:
            return False
        machine = getattr(spec, "state_machine_id", "") or ""
        if machine and str(payload.get("state_machine_id", "") or "") != machine:
            return False
        if not await self._claim(redis, tenant_id, trigger_id, member):
            return False
        key = f"trigger:cond:state:{tenant_id}:{trigger_id}"
        entity = str(payload.get("entity_id", "") or "")
        tracked_raw = await redis.hget(key, entity)
        await redis.hset(key, entity, current)
        await redis.expire(key, self._STATE_TTL_S)
        tracked = tracked_raw.decode() if isinstance(tracked_raw, bytes) else tracked_raw
        prev = str(payload.get("from_state") or "") or (tracked or None)
        if prev == current:
            return False
        to_state = getattr(spec, "to_state", "") or ""
        from_state = getattr(spec, "from_state", "") or ""
        if to_state and current != to_state:
            return False
        return not (from_state and prev != from_state)

    async def _dispatch(self, spec: Any, payload: dict, tenant_id: str) -> None:
        # Plan from the tenant record — never the (partly client-supplied) event.
        tenant_ctx = await event_tenant_ctx(self._dispatcher, tenant_id)
        try:
            await self._dispatcher.dispatch(  # type: ignore[attr-defined]
                spec, payload, tenant_ctx, message_id=str(payload.get("event_id", "") or "")
            )
        except Exception as exc:
            _log.warning("condition_dispatch_error: %s", exc)

    def _safe_eval(self, expr: str, payload: dict) -> bool:
        """Evaluate a sub-condition; an error counts as FALSE (fail closed)."""
        try:
            return bool(self._cel.evaluate(expr, payload))
        except Exception as exc:
            _log.warning("compound_subcondition_error: %s", exc)
            return False

    def _should_fire(
        self,
        ttype: str,
        spec: Any,
        trigger_id: str,
        payload: dict,
        index: dict[str, Any],
    ) -> bool:
        if ttype == "condition":
            expr = getattr(spec, "condition_expression", "") or getattr(spec, "condition", "")
            try:
                return bool(self._cel.evaluate(expr, payload))
            except Exception as exc:
                # Forward a broken expression to the dispatcher: its gate fails
                # closed and audits the skip as ``condition_error`` (a silent drop
                # here would leave the operator no trace of why it never fires).
                _log.warning("condition_eval_error trigger=%s: %s", trigger_id, exc)
                return True

        if ttype == "counter_threshold":
            key = f"{trigger_id}:{getattr(spec, 'counter_key', '') or 'default'}"
            self._counter.record(key)
            window = int(getattr(spec, "counter_window_secs", 3600) or 3600)
            threshold = int(getattr(spec, "counter_threshold", 0) or 0)
            if threshold > 0 and self._counter.check(key, threshold, window):
                self._counter.reset(key)
                return True
            return False

        if ttype == "window_aggregate":
            field = getattr(spec, "window_field", "") or ""
            raw = extract_path(payload, field) if field else None
            try:
                value = float(raw)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return False
            self._window.record(trigger_id, value)
            return self._window.check(
                trigger_id,
                float(getattr(spec, "window_threshold", 0.0) or 0.0),
                getattr(spec, "window_aggregation", "sum") or "sum",
                int(getattr(spec, "window_seconds", 300) or 300),
            )

        if ttype == "state_transition":
            current = str(payload.get("state", "") or "")
            if not current:
                return False
            # A trigger bound to a machine only reacts to THAT machine's
            # transitions (any event carrying a "state" field used to match).
            machine = getattr(spec, "state_machine_id", "") or ""
            if machine and str(payload.get("state_machine_id", "") or "") != machine:
                return False
            key = f"{trigger_id}:{payload.get('entity_id', '')}"
            tracked = self._last_state.get(key)
            self._last_state[key] = current
            # Prefer the publisher's authoritative from_state (state machines
            # publish it) over this consumer's per-process memory.
            prev = str(payload.get("from_state") or "") or tracked
            if prev == current:
                return False  # only on an actual change
            to_state = getattr(spec, "to_state", "") or ""
            from_state = getattr(spec, "from_state", "") or ""
            if to_state and current != to_state:
                return False
            return not (from_state and prev != from_state)

        if ttype == "compound":
            sub_ids = list(getattr(spec, "compound_trigger_ids", []) or [])
            if not sub_ids:
                return False
            states: dict[str, bool] = {}
            for sid in sub_ids:
                sub = index.get(str(sid))
                expr = (
                    (getattr(sub, "condition_expression", "") or getattr(sub, "condition", ""))
                    if sub is not None
                    else ""
                )
                states[str(sid)] = self._safe_eval(expr, payload) if sub is not None else False
            return self._compound.evaluate_compound(
                getattr(spec, "compound_logic", "AND") or "AND", states
            )

        return False


def _spec_of(trigger: Any) -> Any:
    return trigger.get("spec") if isinstance(trigger, dict) else getattr(trigger, "spec", None)
