"""Parent-session wait/resume and live events for durable handoffs.

A handoff transfers control from the delegating (source) agent to the target. While
the target works the parent coordination session is ``paused``; when the handoff
ends — completed, failed, cancelled or expired — the parent is resumed:

* a ``decision`` message from the target to the source is appended to the canonical
  transcript (durable, replayable; the source agent sees the outcome and result
  reference there), and
* the parent session moves ``paused -> active`` through the coordination service,
* and the transitions are published on the live bus (Redis pub/sub across replicas)
  so every connected participant sees them immediately.

Every step is keyed on the handoff id, so repeating it (a retried command, another
replica) is a no-op; that is what makes resumption durable when a first attempt
fails halfway. The Postgres handoff repository additionally writes each transition
to ``coordination_events`` + outbox in the same transaction as the state change.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from app.coordination.contracts import Classification
from app.coordination.handoffs.models import HandoffRecord, HandoffState
from app.coordination.state_machines import InvalidTransitionError
from app.coordination.store import OptimisticConflictError
from app.tenancy.context import PlanTier, TenantContext

logger = structlog.get_logger(__name__)

_CAS_ATTEMPTS = 5

_OUTCOME_TEXT = {
    HandoffState.COMPLETED: "completed",
    HandoffState.FAILED: "failed",
    HandoffState.CANCELLED: "cancelled",
    HandoffState.EXPIRED: "expired",
}


def _system_tenant(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="system:handoff-resumer"
    )


class HandoffParentResumer:
    def __init__(
        self,
        *,
        coordination_service: Callable[[], Any],
        transcript_service: Callable[[], Any],
        live_bus: Callable[[], Any],
    ) -> None:
        # Getters: the app lifespan swaps in DB/Redis-backed services after wiring.
        self._coordination = coordination_service
        self._transcript = transcript_service
        self._bus = live_bus

    async def emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        tenant_id = str(payload.get("tenant_id") or "")
        session_id = str(payload.get("session_id") or "")
        if not tenant_id or not session_id:
            return
        await self._publish(
            tenant_id, session_id, {"type": "event", "event_type": event_type, "payload": payload}
        )

    async def pause_parent(self, record: HandoffRecord) -> None:
        """The source hands control to the target: its session waits."""
        await self._transition_parent(record, target="paused", from_state="active", step="pause")

    async def resume_parent(self, record: HandoffRecord) -> None:
        outcome = _OUTCOME_TEXT.get(record.state, record.state.value)
        result = f" Result: {record.result_reference}." if record.result_reference else ""
        content = (
            f"Handoff {record.handoff_id} {outcome} by {record.target_agent_id} "
            f"→ parent resumed: control returns to {record.source_agent_id}.{result}"
        )
        transcript = self._transcript()
        if transcript is None:
            raise RuntimeError("transcript service unavailable; cannot record handoff resume")
        message = await transcript.append(
            tenant_id=record.tenant_id,
            session_id=record.session_id,
            sender_agent_id=record.target_agent_id,
            recipient_agent_ids=(record.source_agent_id,),
            message_type="decision",
            content=content,
            classification=Classification(record.classification),
            idempotency_key=f"handoff:{record.handoff_id}:parent-resumed",
            provenance_chain=(f"handoff:{record.handoff_id}",),
        )
        session_state = await self._transition_parent(
            record, target="active", from_state="paused", step="resume"
        )
        await self._publish(
            record.tenant_id,
            record.session_id,
            {"type": "message", "message": message.model_dump(mode="json")},
        )
        await self.emit_event(
            "handoff.parent_resumed.v1",
            {
                "tenant_id": record.tenant_id,
                "session_id": record.session_id,
                "handoff_id": record.handoff_id,
                "outcome": outcome,
                "parent_agent_id": record.source_agent_id,
                "session_state": session_state,
                "message_id": message.message_id,
            },
        )

    async def _transition_parent(
        self, record: HandoffRecord, *, target: str, from_state: str, step: str
    ) -> str | None:
        """Move the parent session ``from_state -> target`` once per handoff step.

        A session in any other state (not started, already resumed by an operator,
        cancelling, terminal) is left alone. Returns the session's resulting state.
        """
        service = self._coordination()
        if service is None:
            raise RuntimeError("coordination service unavailable; cannot move parent session")
        tenant = _system_tenant(record.tenant_id)
        command = "pause_session" if target == "paused" else "resume_session"
        for _attempt in range(_CAS_ATTEMPTS):
            try:
                session = await service.get_session(tenant, record.session_id)
            except KeyError:
                logger.warning(
                    "handoff_parent_session_missing",
                    handoff_id=record.handoff_id,
                    session_id=record.session_id,
                )
                return None
            if session.state != from_state:
                return str(session.state)
            try:
                accepted = await getattr(service, command)(
                    tenant,
                    record.session_id,
                    expected_version=session.version,
                    idempotency_key=f"handoff:{record.handoff_id}:{step}",
                )
            except OptimisticConflictError:
                continue
            except InvalidTransitionError:
                return str(session.state)
            return str(accepted.state)
        raise OptimisticConflictError(
            f"parent session {record.session_id} kept changing; handoff {step} not applied"
        )

    async def _publish(self, tenant_id: str, session_id: str, frame: dict[str, Any]) -> None:
        bus = self._bus()
        if bus is None:
            return
        try:
            await bus.publish(tenant_id, session_id, frame)
        except Exception as exc:
            # Live fan-out is best-effort on top of the durable record (transcript,
            # coordination_events); subscribers catch up by replay.
            logger.warning("handoff_live_publish_failed", error=str(exc))


__all__ = ["HandoffParentResumer"]
