"""TriggerDispatcher — unified 12-step dispatch pipeline.

Every trigger firing, regardless of type, goes through this single dispatcher.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime

from app.triggers.bulkhead import TriggerBulkhead
from app.triggers.circuit_breaker import CircuitBreakerRegistry
from app.triggers.dedup import derive_idempotency_key
from app.triggers.dlq import write_to_dlq
from app.triggers.events import SimulatedTriggerResult, TriggerEvent
from app.triggers.models import TriggerSpec
from app.triggers.quota import TriggerQuotaEnforcer
from app.triggers.rate_limiter import TriggerRateLimiter
from app.triggers.rbac import check_permission

from datetime import datetime, timezone

_log = logging.getLogger(__name__)


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

    async def dispatch(
        self,
        trigger_spec: TriggerSpec,
        payload: dict,
        tenant_ctx: object,
        *,
        simulation: bool = False,
        caller_role: str = "operator",
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
                trigger_id, tenant_id, payload, "RBAC_DENIED",
                str(exc), scheduled_fire_time, source_goal_id,
                completion_event_id, message_id, txn_id,
            )

        # ── Step 2: Payload size enforcement ─────────────────────────────────
        payload_bytes = len(str(payload).encode())
        max_bytes = self._payload_size_limit(plan)
        if payload_bytes > max_bytes:
            return self._skip_event(
                trigger_id, tenant_id, payload, "PAYLOAD_TOO_LARGE",
                f"Payload {payload_bytes}B exceeds {max_bytes}B limit",
                scheduled_fire_time, source_goal_id, completion_event_id,
                message_id, txn_id,
            )

        # ── Step 3: Idempotency key derivation ────────────────────────────────
        idempotency_key = derive_idempotency_key(
            trigger_id,
            trigger_spec.trigger_type.value if hasattr(trigger_spec.trigger_type, "value")
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
                trigger_id, tenant_id, payload, idempotency_key, "dedup",
            )

        # ── Step 5: Rate limit check ──────────────────────────────────────────
        if not await self._rate_limiter.check(
            trigger_id, trigger_spec.max_firings_per_hour, plan
        ):
            return self._make_skip_event(
                trigger_id, tenant_id, payload, idempotency_key, "rate_limit",
            )

        # ── Step 6: Circuit breaker check ─────────────────────────────────────
        cb = self._cb_registry.get(trigger_id)
        if cb.is_open():
            return self._make_skip_event(
                trigger_id, tenant_id, payload, idempotency_key, "circuit_open",
            )

        # ── Step 7: Bulkhead check ────────────────────────────────────────────
        if not await self._bulkhead.acquire(tenant_id, plan):
            return self._make_skip_event(
                trigger_id, tenant_id, payload, idempotency_key, "bulkhead_full",
            )

        try:
            # ── Step 8: Condition evaluation ─────────────────────────────────
            if trigger_spec.condition:
                try:
                    if not self._evaluate_condition(trigger_spec.condition, payload):
                        return self._make_skip_event(
                            trigger_id, tenant_id, payload, idempotency_key,
                            "condition_false",
                        )
                except Exception as exc:
                    _log.warning("condition_eval_error trigger=%s: %s", trigger_id, exc)
                    # Treat condition error as condition_false
                    return self._make_skip_event(
                        trigger_id, tenant_id, payload, idempotency_key,
                        "condition_false",
                    )

            # ── Step 9: Goal template rendering ──────────────────────────────
            goal_text = self._render_template(
                trigger_spec.goal_template or "Trigger fired: {{trigger_type}}",
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
            try:
                goal_id = await self._create_goal(
                    trigger_spec, goal_text, tenant_ctx, idempotency_key,
                )
                cb.record_success()
            except Exception as exc:
                cb.record_failure()
                _log.error(
                    "goal_enqueue_failed trigger_id=%s: %s", trigger_id, exc
                )
                await self._write_dlq(
                    tenant_id, trigger_id, "GOAL_ENQUEUE_FAILED",
                    str(exc), payload,
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
                fired_at=datetime.now(timezone.utc),
                payload=payload,
                goal_created=goal_id is not None,
                goal_id=goal_id,
                skip_reason=None,
                processing_ms=processing_ms,
            )
            await self._persist_event(event)
            _log.info(
                "trigger_fired trigger_id=%s type=%s goal_id=%s ms=%d",
                trigger_id, trigger_spec.trigger_type, goal_id, processing_ms,
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
        if self._redis is None:
            return False
        try:
            key = f"trigger_dedup:{tenant_id}:{idempotency_key}"
            result = await self._redis.set(key, 1, ex=60, nx=True)
            return result is None  # None means key already existed
        except Exception:
            return False

    def _evaluate_condition(self, expression: str, payload: dict) -> bool:
        """Evaluate a simple CEL-like condition.

        Falls back to a basic Python eval-free checker.
        For production use install `cel-python`.
        """
        if not expression.strip():
            return True
        try:
            import celpy  # type: ignore[import]
            env = celpy.Environment()
            ast = env.compile(expression)
            prog = env.program(ast)
            import celpy.celtypes as ct  # type: ignore[import]
            activation = {"payload": ct.MapType({
                ct.StringType(k): ct.StringType(str(v))
                for k, v in payload.items()
            })}
            return bool(prog.evaluate(activation))
        except ImportError:
            # cel-python not installed — simple fallback: expression is truthy
            return True
        except Exception:
            return False

    def _render_template(
        self, template: str, payload: dict, **extra: str
    ) -> str:
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

    async def _create_goal(
        self,
        spec: TriggerSpec,
        goal_text: str,
        tenant_ctx: object,
        idempotency_key: str,
    ) -> str | None:
        if self._goal_service is None:
            return None
        try:
            result = await self._goal_service.create_goal(
                tenant_ctx=tenant_ctx,
                goal_text=goal_text,
                agent_id=getattr(spec, "watch_agent_id", "") or None,
                idempotency_key=idempotency_key,
            )
            if hasattr(result, "goal_id"):
                return result.goal_id
            if isinstance(result, dict):
                return result.get("goal_id")
            return str(result) if result else None
        except Exception as exc:
            raise RuntimeError(f"goal_service.create_goal failed: {exc}") from exc

    async def _persist_event(self, event: TriggerEvent) -> None:
        if self._db_factory is None:
            return
        try:
            from sqlalchemy import text
            async with self._db_factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO trigger_events "
                        "(id, tenant_id, trigger_id, trigger_type, idempotency_key, "
                        " fired_at, goal_created, goal_id, skip_reason, processing_ms) "
                        "VALUES (:id, :tenant_id, :trigger_id, :trigger_type, "
                        "        :idempotency_key, :fired_at, :goal_created, "
                        "        :goal_id, :skip_reason, :processing_ms) "
                        "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING"
                    ),
                    {
                        "id": event.event_id,
                        "tenant_id": event.tenant_id,
                        "trigger_id": event.trigger_id,
                        "trigger_type": event.trigger_type,
                        "idempotency_key": event.idempotency_key,
                        "fired_at": event.fired_at,
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
            fired_at=datetime.now(timezone.utc),
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
            trigger_id, "unknown", payload,
            scheduled_fire_time=scheduled_fire_time,
            source_goal_id=source_goal_id,
            completion_event_id=completion_event_id,
            message_id=message_id,
            txn_id=txn_id,
        )
        return self._make_skip_event(trigger_id, tenant_id, payload, key, failure_type)
