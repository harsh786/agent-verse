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

import json
import logging
from types import SimpleNamespace
from typing import Any

from app.triggers.condition.evaluator import (
    CELEvaluator,
    CompoundTriggerEvaluator,
    CounterThresholdEvaluator,
    WindowAggregateEvaluator,
)
from app.triggers.consumers.event import CHANNEL_PATTERN, CHANNEL_PREFIX, _decode
from app.triggers.polling import extract_path

_log = logging.getLogger(__name__)

_FAMILY_TYPES = (
    "condition",
    "counter_threshold",
    "compound",
    "state_transition",
    "window_aggregate",
)


class ConditionTriggerConsumer:
    """Evaluates Family D (condition/state) triggers on the EVENT bus."""

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
            pubsub = self._redis.pubsub()  # type: ignore[attr-defined]
            await pubsub.psubscribe(CHANNEL_PATTERN)
            _log.info("condition_consumer_started pattern=%s", CHANNEL_PATTERN)
            async for message in pubsub.listen():
                if not self._running:
                    break
                if message.get("type") != "pmessage":
                    continue
                await self._handle(message)
        except Exception as exc:  # pragma: no cover - defensive
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
        for ttype in _FAMILY_TYPES:
            try:
                triggers = await self._store.find_by_type_async(  # type: ignore[attr-defined]
                    ttype, tenant_id=tenant_id
                )
            except Exception as exc:
                _log.warning("condition_store_error type=%s: %s", ttype, exc)
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
                    if self._should_fire(ttype, spec, trigger_id, payload, index):
                        await self._dispatch(spec, payload, tenant_id)
                except Exception as exc:
                    _log.warning("condition_eval_error type=%s: %s", ttype, exc)

    async def _dispatch(self, spec: Any, payload: dict, tenant_id: str) -> None:
        tenant_ctx = SimpleNamespace(tenant_id=tenant_id, plan=payload.get("tenant_plan", "free"))
        try:
            await self._dispatcher.dispatch(  # type: ignore[attr-defined]
                spec, payload, tenant_ctx, message_id=str(payload.get("event_id", "") or "")
            )
        except Exception as exc:
            _log.warning("condition_dispatch_error: %s", exc)

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
            return self._cel.evaluate(expr, payload)

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
            prev = self._last_state.get(trigger_id)
            self._last_state[trigger_id] = current
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
                states[str(sid)] = self._cel.evaluate(expr, payload) if sub is not None else False
            return self._compound.evaluate_compound(
                getattr(spec, "compound_logic", "AND") or "AND", states
            )

        return False


def _spec_of(trigger: Any) -> Any:
    return trigger.get("spec") if isinstance(trigger, dict) else getattr(trigger, "spec", None)
