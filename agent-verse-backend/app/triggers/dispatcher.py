"""TriggerDispatcher — unified 12-step dispatch pipeline.

Every trigger firing, regardless of type, goes through this single dispatcher.
"""

from __future__ import annotations

import json as _json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from app.triggers.bulkhead import TriggerBulkhead
from app.triggers.circuit_breaker import CircuitBreakerRegistry
from app.triggers.dedup import derive_idempotency_key
from app.triggers.dlq import write_to_dlq
from app.triggers.events import SimulatedTriggerResult, TriggerEvent
from app.triggers.models import TriggerSpec
from app.triggers.quota import TriggerQuotaEnforcer
from app.triggers.rate_limiter import TriggerRateLimiter
from app.triggers.rbac import SYSTEM_ROLE, check_permission

_log = logging.getLogger(__name__)

# Redis dedup window. Short by design — the durable
# `trigger_events` UNIQUE (tenant_id, idempotency_key) row is what
# catches a replay after this expires.
_DEDUP_TTL_SECONDS = 60

# Cap on the inbound payload copy kept in the trigger_events audit row. A webhook
# body is attacker-sized; the audit trail needs enough to explain *what* fired,
# not an unbounded blob replicated into an append-only table that a busy tenant
# writes to on every single firing.
_AUDIT_PAYLOAD_MAX_BYTES = 16_384


def _payload_for_audit(payload: Any) -> dict[str, Any]:
    """Return a JSON-serialisable, size-capped copy of an inbound payload."""
    if not isinstance(payload, dict):
        return {"_value": str(payload)[:_AUDIT_PAYLOAD_MAX_BYTES]}
    try:
        encoded = _json.dumps(payload)
    except (TypeError, ValueError):
        return {"_unserializable": str(payload)[:_AUDIT_PAYLOAD_MAX_BYTES]}
    if len(encoded) <= _AUDIT_PAYLOAD_MAX_BYTES:
        return payload
    return {
        "_truncated": True,
        "_original_bytes": len(encoded),
        "_preview": encoded[:_AUDIT_PAYLOAD_MAX_BYTES],
    }


# Trigger types fired by a goal's own lifecycle event. On these ``watch_agent_id``
# is the SOURCE filter (whose goals to watch), never the agent to run, and the
# created goal is stamped with the trigger's id so its completion cannot re-fire
# the same trigger.
_GOAL_EVENT_TRIGGER_TYPES = frozenset({"goal_completed", "goal_failed", "goal_score_below"})


def _trigger_type_value(spec: object) -> str:
    tt = getattr(spec, "trigger_type", "")
    return str(getattr(tt, "value", tt) or "")


def _run_agent_id(spec: object) -> str:
    """The agent a firing routes its goal to (``""`` = auto-route).

    ``agent_id`` is the trigger's referenced agent. ``watch_agent_id`` is still
    honoured as the run target for non-goal-event types (the beat path and
    legacy specs carry the referenced agent there), but on goal-event triggers
    it only filters which goals are watched.
    """
    agent = str(getattr(spec, "agent_id", "") or "")
    if agent or _trigger_type_value(spec) in _GOAL_EVENT_TRIGGER_TYPES:
        return agent
    return str(getattr(spec, "watch_agent_id", "") or "")


def _chain_depth(payload: object) -> int:
    """``trigger_chain_depth`` from a chained event payload (0 when absent/bad)."""
    if not isinstance(payload, dict):
        return 0
    try:
        return max(0, int(payload.get("trigger_chain_depth", 0) or 0))
    except (TypeError, ValueError):
        return 0


class TriggerDispatcher:
    """Single entry point for all trigger dispatch operations."""

    def __init__(
        self,
        *,
        goal_service: object | None = None,
        db_session_factory: object | None = None,
        redis: object | None = None,
    ) -> None:
        self._goal_service = goal_service
        self._db_factory = db_session_factory
        self._rate_limiter = TriggerRateLimiter(redis=redis)
        self._bulkhead = TriggerBulkhead(redis=redis)
        self._cb_registry = CircuitBreakerRegistry()
        self._quota = TriggerQuotaEnforcer()
        self._redis = redis

    async def resolve_tenant_plan(self, tenant_id: str) -> Any:
        """The tenant's plan tier from the tenant record (FREE when unknown).

        Event-bus consumers build their tenant context from this instead of the
        event payload, which a client can partly control (TRG-05).
        """
        from app.tenancy.plan_resolver import resolve_tenant_plan

        return await resolve_tenant_plan(tenant_id, db_factory=self._db_factory)

    async def dispatch(
        self,
        trigger_spec: TriggerSpec,
        payload: dict,
        tenant_ctx: object,
        *,
        simulation: bool = False,
        caller_role: str = SYSTEM_ROLE,
        scheduled_fire_time: str | None = None,
        source_goal_id: str | None = None,
        completion_event_id: str | None = None,
        message_id: str | None = None,
        txn_id: str | None = None,
    ) -> TriggerEvent | SimulatedTriggerResult:
        """Execute the 12-step dispatch pipeline, auditing suppressed fires.

        ``caller_role`` is a trigger-matrix role (``app.triggers.rbac``). Automated
        fires (beat, event-bus consumers, authenticated webhooks) run as
        ``system``; a MANUAL fire must pass ``trigger_role(tenant_ctx)`` — the
        default used to be ``operator``, so the RBAC step never denied anyone.

        Every skip outcome (RBAC, payload size, dedup, rate limit, circuit open,
        bulkhead full, condition false) used to be returned to the caller and
        dropped: ``trigger_events`` only ever recorded fires that created a goal,
        so a suppressed third-party delivery left no trace at all.
        """
        result = await self._dispatch_pipeline(
            trigger_spec,
            payload,
            tenant_ctx,
            simulation=simulation,
            caller_role=caller_role,
            scheduled_fire_time=scheduled_fire_time,
            source_goal_id=source_goal_id,
            completion_event_id=completion_event_id,
            message_id=message_id,
            txn_id=txn_id,
        )
        if isinstance(result, TriggerEvent) and result.skip_reason and not simulation:
            if result.trigger_type == "unknown":
                result.trigger_type = str(trigger_spec.trigger_type)
            await self._persist_skip_event(result)
        return result

    async def _persist_skip_event(self, event: TriggerEvent) -> None:
        """Record a suppressed firing without interfering with dedup.

        ``trigger_events`` has UNIQUE (tenant_id, idempotency_key), and that key
        is also the durable dedup gate (``_already_fired`` matches it exactly).
        A skip row stored under the firing's real key would either collide with
        the original fire (a dedup skip is by definition a repeat of a key that
        already fired) or — worse, for a rate-limited / circuit-open / bulkhead
        skip — permanently mark a firing that never ran as "already fired", so
        its legitimate retry would be deduped forever. Skip rows are therefore
        stored under ``<key>:skip:<event_id>``: unique per skip, still
        prefix-searchable by the original key, and never matched by the gate.
        Every skip kind is audited, including dedup (replayed deliveries are
        exactly what an operator investigating a webhook wants to see).
        """
        import dataclasses

        await self._persist_event(
            dataclasses.replace(
                event, idempotency_key=f"{event.idempotency_key}:skip:{event.event_id}"
            )
        )

    async def _dispatch_pipeline(
        self,
        trigger_spec: TriggerSpec,
        payload: dict,
        tenant_ctx: object,
        *,
        simulation: bool = False,
        caller_role: str = SYSTEM_ROLE,
        scheduled_fire_time: str | None = None,
        source_goal_id: str | None = None,
        completion_event_id: str | None = None,
        message_id: str | None = None,
        txn_id: str | None = None,
    ) -> TriggerEvent | SimulatedTriggerResult:
        """Execute the 12-step dispatch pipeline."""
        start_ms = time.monotonic() * 1000
        trigger_id = getattr(trigger_spec, "trigger_id", "unknown")
        tenant_id = getattr(tenant_ctx, "tenant_id", "unknown")
        plan = getattr(tenant_ctx, "plan", "free")

        # ── Step 1: RBAC check ────────────────────────────────────────────────
        try:
            check_permission(caller_role, "fire")
        except Exception as exc:
            return self._skip_event(
                trigger_id,
                tenant_id,
                payload,
                "RBAC_DENIED",
                str(exc),
                scheduled_fire_time,
                source_goal_id,
                completion_event_id,
                message_id,
                txn_id,
            )

        # ── Step 2: Payload size enforcement ─────────────────────────────────
        payload_bytes = len(str(payload).encode())
        max_bytes = self._payload_size_limit(plan)
        if payload_bytes > max_bytes:
            return self._skip_event(
                trigger_id,
                tenant_id,
                payload,
                "PAYLOAD_TOO_LARGE",
                f"Payload {payload_bytes}B exceeds {max_bytes}B limit",
                scheduled_fire_time,
                source_goal_id,
                completion_event_id,
                message_id,
                txn_id,
            )

        # ── Step 3: Idempotency key derivation ────────────────────────────────
        idempotency_key = derive_idempotency_key(
            trigger_id,
            trigger_spec.trigger_type.value
            if hasattr(trigger_spec.trigger_type, "value")
            else str(trigger_spec.trigger_type),
            payload,
            scheduled_fire_time=scheduled_fire_time,
            source_goal_id=source_goal_id,
            completion_event_id=completion_event_id,
            message_id=message_id,
            txn_id=txn_id,
        )

        # ── Step 4: Deduplication check ───────────────────────────────────────
        if await self._is_duplicate(idempotency_key, tenant_id):
            return self._make_skip_event(
                trigger_id,
                tenant_id,
                payload,
                idempotency_key,
                "dedup",
            )

        # ── Step 5: Rate limit check ──────────────────────────────────────────
        if not await self._rate_limiter.check(trigger_id, trigger_spec.max_firings_per_hour, plan):
            return self._make_skip_event(
                trigger_id,
                tenant_id,
                payload,
                idempotency_key,
                "rate_limit",
            )

        # ── Step 6: Circuit breaker check ─────────────────────────────────────
        cb = self._cb_registry.get(trigger_id)
        if cb.is_open():
            return self._make_skip_event(
                trigger_id,
                tenant_id,
                payload,
                idempotency_key,
                "circuit_open",
            )

        # ── Step 7: Bulkhead check ────────────────────────────────────────────
        if not await self._bulkhead.acquire(tenant_id, plan):
            return self._make_skip_event(
                trigger_id,
                tenant_id,
                payload,
                idempotency_key,
                "bulkhead_full",
            )

        try:
            # ── Step 8: Condition evaluation ─────────────────────────────────
            # CONDITION triggers store their expression in ``condition_expression``
            # (the API maps ``condition_cel`` there); ``condition`` is the legacy /
            # generic gate. Reading only ``condition`` let every event fire every
            # CONDITION trigger. Every configured expression must hold; a parse or
            # evaluation error fails CLOSED and is audited as ``condition_error``.
            expressions = [
                e
                for e in (
                    getattr(trigger_spec, "condition_expression", "") or "",
                    trigger_spec.condition or "",
                )
                if e.strip()
            ]
            for expression in expressions:
                try:
                    holds = self._evaluate_condition(expression, payload)
                except Exception as exc:
                    _log.warning("condition_eval_error trigger=%s: %s", trigger_id, exc)
                    return self._make_skip_event(
                        trigger_id,
                        tenant_id,
                        payload,
                        idempotency_key,
                        "condition_error",
                    )
                if not holds:
                    return self._make_skip_event(
                        trigger_id,
                        tenant_id,
                        payload,
                        idempotency_key,
                        "condition_false",
                    )

            # ── Step 9: Goal template rendering ──────────────────────────────
            # Precedence: the trigger's own goal template → the referenced agent's
            # configured goal (so a trigger can simply reference an agent without
            # re-authoring a goal) → a generic default.
            _template = (trigger_spec.goal_template or "").strip()
            _ref_agent = _run_agent_id(trigger_spec)
            if not _template and _ref_agent:
                _template = self._agent_goal_template(_ref_agent, tenant_ctx)
            goal_text = self._render_template(
                _template or "Trigger fired: {{trigger_type}}",
                payload,
                trigger_type=str(trigger_spec.trigger_type),
            )

            # ── Step 10: Simulation short-circuit ─────────────────────────────
            if simulation or trigger_spec.simulation_mode:
                return SimulatedTriggerResult(
                    trigger_id=trigger_id,
                    trigger_type=str(trigger_spec.trigger_type),
                    would_have_fired=True,
                    skip_reason=None,
                    goal_template_rendered=goal_text,
                    condition_evaluated=None,
                    estimated_cost_usd=None,
                )

            # ── Step 11: Goal creation ────────────────────────────────────────
            goal_id: str | None = None
            goal_deduplicated = False
            try:
                _created = await self._create_goal(
                    trigger_spec,
                    goal_text,
                    tenant_ctx,
                    idempotency_key,
                    chain_depth=_chain_depth(payload),
                )
                if isinstance(_created, tuple):
                    goal_id, goal_deduplicated = _created
                else:  # overridden/mocked _create_goal returning just the id
                    goal_id = _created
                cb.record_success()
            except Exception as exc:
                cb.record_failure()
                _log.error("goal_enqueue_failed trigger_id=%s: %s", trigger_id, exc)
                await self._write_dlq(
                    tenant_id,
                    trigger_id,
                    "GOAL_ENQUEUE_FAILED",
                    str(exc),
                    payload,
                )
                goal_id = None

            # ── Step 12: Persist TriggerEvent ─────────────────────────────────
            processing_ms = int(time.monotonic() * 1000 - start_ms)
            event = TriggerEvent(
                event_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                trigger_id=trigger_id,
                trigger_type=str(trigger_spec.trigger_type),
                idempotency_key=idempotency_key,
                fired_at=datetime.now(UTC),
                payload=payload,
                # Folded into an identical goal already in progress: no goal was
                # created by this firing (it used to report goal_created=True).
                goal_created=goal_id is not None and not goal_deduplicated,
                goal_id=goal_id,
                skip_reason="goal_in_progress" if goal_deduplicated else None,
                processing_ms=processing_ms,
            )
            await self._persist_event(event)
            _log.info(
                "trigger_fired trigger_id=%s type=%s goal_id=%s ms=%d",
                trigger_id,
                trigger_spec.trigger_type,
                goal_id,
                processing_ms,
            )
            return event

        finally:
            await self._bulkhead.release(tenant_id)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _payload_size_limit(self, plan: str) -> int:
        return {
            "free": 64 * 1024,
            "starter": 256 * 1024,
            "professional": 1024 * 1024,
            "enterprise": 4096 * 1024,
        }.get(plan, 64 * 1024)

    async def _is_duplicate(self, idempotency_key: str, tenant_id: str) -> bool:
        """Two-layer dedup: Redis for the race, Postgres for the replay.

        The Redis SET NX is atomic, so it settles concurrent dispatches across
        replicas — but its key expires after `_DEDUP_TTL_SECONDS`. Anything that
        re-delivers the SAME firing later than that (a consumer retry, a Celery
        redelivery, a cron occurrence retried after a worker restart, a Redis
        failover that drops keys) sailed straight through and created a second
        goal.

        `trigger_events` already carries UNIQUE (tenant_id, idempotency_key) for
        exactly this, but it was only ever written AFTER the goal was created,
        with `ON CONFLICT DO NOTHING` and the result discarded — an audit row,
        not a gate. So a replayed firing produced two goals and ONE audit row,
        which also makes the duplicate invisible after the fact.
        """
        if self._redis is not None:
            try:
                key = f"trigger_dedup:{tenant_id}:{idempotency_key}"
                result = await self._redis.set(key, 1, ex=_DEDUP_TTL_SECONDS, nx=True)
                if result is None:  # key already existed → duplicate
                    return True
            except Exception as exc:
                # Redis is configured but transiently unavailable. FAIL CLOSED —
                # treat this as a duplicate so a Redis blip cannot let each of the
                # N pods (which all receive the same broadcast pub/sub event)
                # launch the same autonomous goal. Missing one fire during an
                # outage is far safer than N-fold execution; schedules re-fire on
                # the next tick.
                logging.getLogger(__name__).warning(
                    "trigger_dedup_redis_unavailable_failing_closed: %s", str(exc)[:200]
                )
                return True

        # Durable backstop for a firing replayed after the Redis key expired.
        return await self._already_fired(idempotency_key, tenant_id)

    async def _already_fired(self, idempotency_key: str, tenant_id: str) -> bool:
        """Has this exact firing already been recorded in `trigger_events`?

        Deliberately fails OPEN on a DB error, unlike the Redis gate above. Redis
        is the primary gate and already failed closed; this only covers the
        narrow post-TTL replay window. Failing closed here would halt every
        trigger in the system on a Postgres blip, which is worse than the
        duplicate it would prevent.
        """
        if self._db_factory is None:
            return False
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db_factory() as session,  # type: ignore[operator]
                sqlalchemy_rls_context(session, tenant_id),
            ):
                row = (
                    await session.execute(
                        text(
                            "SELECT 1 FROM trigger_events "
                            "WHERE tenant_id = :t AND idempotency_key = :k LIMIT 1"
                        ),
                        {"t": tenant_id, "k": idempotency_key},
                    )
                ).fetchone()
            return row is not None
        except Exception as exc:
            _log.warning("trigger_dedup_durable_check_failed: %s", str(exc)[:200])
            return False

    def _evaluate_condition(self, expression: str, payload: dict) -> bool:
        """Evaluate a CEL condition; raises on a parse/evaluation error.

        Delegates to ``CELEvaluator`` (cel-python when installed, else the
        dependency-free fail-closed ``safe_eval``). The previous inline version
        returned True whenever cel-python was missing — i.e. always.
        """
        from app.triggers.condition.evaluator import CELEvaluator

        return CELEvaluator().evaluate(expression, payload)

    def _render_template(self, template: str, payload: dict, **extra: str) -> str:
        """Render a Jinja2 sandboxed goal template."""
        import re

        result = template
        # Replace {{payload.field}} with payload values
        for match in re.finditer(r"\{\{payload\.([^}]+)\}\}", template):
            field = match.group(1)
            value = str(payload.get(field, ""))
            result = result.replace(match.group(0), value)
        # Replace {{trigger_type}} etc.
        for k, v in extra.items():
            result = result.replace(f"{{{{{k}}}}}", v)
        return result[:2048]  # max rendered length

    def _agent_goal_template(self, agent_id: str, tenant_ctx: object) -> str:
        """Return a referenced agent's own goal_template, or "" if unavailable.

        Lets a trigger that only references an agent run that agent's configured
        goal without the operator re-authoring a goal template on the trigger.
        """
        gs = self._goal_service
        if gs is None or not hasattr(gs, "_get_agent_store"):
            return ""
        try:
            store = gs._get_agent_store()  # type: ignore[attr-defined]
            if store is None:
                return ""
            rec = store.get(agent_id, tenant_ctx=tenant_ctx)
            if isinstance(rec, dict):
                return (rec.get("goal_template") or "").strip()
        except Exception as exc:  # never block a fire on agent lookup
            _log.warning("trigger_agent_goal_lookup_failed agent=%s: %s", agent_id, exc)
        return ""

    async def _create_goal(
        self,
        spec: TriggerSpec,
        goal_text: str,
        tenant_ctx: object,
        idempotency_key: str,
        chain_depth: int = 0,
    ) -> tuple[str | None, bool]:
        """Create the goal; returns (goal_id, deduplicated_into_an_existing_goal)."""
        if self._goal_service is None:
            return None, False
        # Only chained firings carry a depth (keeps the create_goal call shape
        # unchanged for every other trigger family).
        extra: dict[str, Any] = {"trigger_chain_depth": chain_depth} if chain_depth else {}
        # Goal-event triggers stamp the created goal with their own id; the goal's
        # lifecycle event carries it back so ChainTriggerConsumer never re-fires
        # this trigger on a goal it created.
        if _trigger_type_value(spec) in _GOAL_EVENT_TRIGGER_TYPES:
            trigger_id = str(getattr(spec, "trigger_id", "") or "")
            if trigger_id:
                extra["source_trigger_id"] = trigger_id
        try:
            result = await self._goal_service.create_goal(
                tenant_ctx=tenant_ctx,
                goal_text=goal_text,
                # Route the fired goal to the agent the trigger references.
                agent_id=_run_agent_id(spec) or None,
                idempotency_key=idempotency_key,
                **extra,
            )
            if hasattr(result, "goal_id"):
                return result.goal_id, bool(getattr(result, "deduplicated", False))
            if isinstance(result, dict):
                return result.get("goal_id"), bool(result.get("deduplicated"))
            return (str(result) if result else None), False
        except Exception as exc:
            raise RuntimeError(f"goal_service.create_goal failed: {exc}") from exc

    async def _persist_event(self, event: TriggerEvent) -> None:
        if self._db_factory is None:
            return
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db_factory() as session,
                sqlalchemy_rls_context(session, event.tenant_id),
            ):
                await session.execute(
                    text(
                        "INSERT INTO trigger_events "
                        "(id, tenant_id, trigger_id, trigger_type, idempotency_key, "
                        " fired_at, payload, goal_created, goal_id, skip_reason, "
                        " processing_ms) "
                        "VALUES (:id, :tenant_id, :trigger_id, :trigger_type, "
                        "        :idempotency_key, :fired_at, CAST(:payload AS json), "
                        "        :goal_created, :goal_id, :skip_reason, :processing_ms) "
                        "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING"
                    ),
                    {
                        "id": event.event_id,
                        "tenant_id": event.tenant_id,
                        "trigger_id": event.trigger_id,
                        "trigger_type": event.trigger_type,
                        "idempotency_key": event.idempotency_key,
                        # The column is NOT NULL with a '{}' default and is read
                        # back by GET /triggers/{id}/events; leaving it out made
                        # every firing's payload permanently '{}' — the audit row
                        # recorded that something fired but never what.
                        "payload": _json.dumps(_payload_for_audit(event.payload)),
                        # trigger_events.fired_at is a naive timestamp column; bind
                        # a naive UTC value so asyncpg does not reject the tz-aware one
                        # (which silently dropped every trigger audit row).
                        "fired_at": (
                            event.fired_at.replace(tzinfo=None)
                            if getattr(event.fired_at, "tzinfo", None) is not None
                            else event.fired_at
                        ),
                        "goal_created": event.goal_created,
                        "goal_id": event.goal_id,
                        "skip_reason": event.skip_reason,
                        "processing_ms": event.processing_ms,
                    },
                )
                await session.commit()
        except Exception as exc:
            _log.warning("persist_event_failed event_id=%s: %s", event.event_id, exc)

    async def _write_dlq(
        self,
        tenant_id: str,
        trigger_id: str,
        failure_type: str,
        error_message: str,
        payload: dict,
    ) -> None:
        if self._db_factory is None:
            return
        try:
            async with self._db_factory() as session:
                await write_to_dlq(
                    session,
                    tenant_id=tenant_id,
                    trigger_id=trigger_id,
                    failure_type=failure_type,
                    error_message=error_message,
                    raw_payload=payload,
                )
        except Exception as exc:
            _log.warning("dlq_write_failed trigger_id=%s: %s", trigger_id, exc)

    def _make_skip_event(
        self,
        trigger_id: str,
        tenant_id: str,
        payload: dict,
        idempotency_key: str,
        skip_reason: str,
    ) -> TriggerEvent:
        _log.debug("trigger_skipped trigger_id=%s reason=%s", trigger_id, skip_reason)
        return TriggerEvent(
            event_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            trigger_id=trigger_id,
            trigger_type="unknown",
            idempotency_key=idempotency_key,
            fired_at=datetime.now(UTC),
            payload=payload,
            goal_created=False,
            goal_id=None,
            skip_reason=skip_reason,
        )

    def _skip_event(
        self,
        trigger_id: str,
        tenant_id: str,
        payload: dict,
        failure_type: str,
        error_msg: str,
        scheduled_fire_time: str | None,
        source_goal_id: str | None,
        completion_event_id: str | None,
        message_id: str | None,
        txn_id: str | None,
    ) -> TriggerEvent:
        key = derive_idempotency_key(
            trigger_id,
            "unknown",
            payload,
            scheduled_fire_time=scheduled_fire_time,
            source_goal_id=source_goal_id,
            completion_event_id=completion_event_id,
            message_id=message_id,
            txn_id=txn_id,
        )
        return self._make_skip_event(trigger_id, tenant_id, payload, key, failure_type)
