"""Authorization and transaction boundary for true agent handoffs."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from app.coordination.contracts import Classification
from app.coordination.handoffs.models import HandoffRecord, HandoffState
from app.coordination.handoffs.repository import HandoffRepository


class HandoffMembership(Protocol):
    async def active_member(self, tenant_id: str, civilization_id: str, agent_id: str) -> bool: ...

    async def connector_allowlist(
        self, tenant_id: str, civilization_id: str, agent_id: str
    ) -> frozenset[str]: ...


class HandoffService:
    def __init__(
        self,
        repository: HandoffRepository,
        *,
        membership: HandoffMembership,
        authorize_target: Callable[[HandoffRecord], Any] = lambda _record: True,
        emit_event: Callable[[str, dict[str, Any]], Any] = lambda _event, _payload: None,
        resume_parent: Callable[[HandoffRecord], Any] = lambda _record: None,
        pause_parent: Callable[[HandoffRecord], Any] = lambda _record: None,
    ) -> None:
        self._repository = repository
        self._membership = membership
        self._authorize_target = authorize_target
        self._emit = emit_event
        self._resume = resume_parent
        self._pause = pause_parent
        # Process-local "already applied" markers only; pause/resume callbacks are
        # idempotent per handoff, so another replica (or a retry after a failed
        # callback) safely re-applies them on a replayed transition.
        self._paused: set[str] = set()
        self._resumed: set[str] = set()

    @staticmethod
    async def _invoke(callback: Any, *args: Any) -> Any:
        value = callback(*args)
        return await value if inspect.isawaitable(value) else value

    async def request(
        self,
        *,
        tenant_id: str,
        session_id: str,
        civilization_id: str,
        source_agent_id: str,
        target_agent_id: str,
        task_summary: str,
        remaining_budget_usd: float,
        deadline: datetime,
        acceptance_token: str,
        idempotency_key: str,
        context_message_ids: tuple[str, ...] = (),
        artifact_refs: tuple[str, ...] = (),
        classification: Classification = Classification.INTERNAL,
    ) -> HandoffRecord:
        if deadline <= datetime.now(UTC):
            raise PermissionError("handoff deadline expired")
        source_active, target_active = await asyncio.gather(
            self._membership.active_member(tenant_id, civilization_id, source_agent_id),
            self._membership.active_member(tenant_id, civilization_id, target_agent_id),
        )
        if not source_active or not target_active:
            raise PermissionError("handoff participants must be active civilization members")
        source_tools, target_tools = await asyncio.gather(
            self._membership.connector_allowlist(tenant_id, civilization_id, source_agent_id),
            self._membership.connector_allowlist(tenant_id, civilization_id, target_agent_id),
        )
        now = datetime.now(UTC)
        record = HandoffRecord(
            handoff_id=uuid.uuid5(
                uuid.NAMESPACE_URL, f"{tenant_id}:{session_id}:{idempotency_key}"
            ).hex,
            tenant_id=tenant_id,
            session_id=session_id,
            civilization_id=civilization_id,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            task_summary=task_summary,
            context_message_ids=context_message_ids,
            artifact_refs=artifact_refs,
            connector_allowlist=source_tools & target_tools,
            classification=classification,
            remaining_budget_usd=remaining_budget_usd,
            deadline=deadline,
            acceptance_token_digest=hashlib.sha256(acceptance_token.encode()).hexdigest(),
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )
        accepted, replayed = await self._repository.create(record)
        if not replayed:
            await self._invoke(
                self._emit,
                "handoff.requested.v1",
                {
                    "tenant_id": tenant_id,
                    "session_id": session_id,
                    "handoff_id": accepted.handoff_id,
                    "source_agent_id": source_agent_id,
                    "target_agent_id": target_agent_id,
                },
            )
        return accepted

    async def accept(
        self,
        tenant_id: str,
        handoff_id: str,
        *,
        token: str,
        expected_version: int,
        idempotency_key: str,
    ) -> HandoffRecord:
        record = await self._required(tenant_id, handoff_id)
        if datetime.now(UTC) >= record.deadline:
            return await self._transition(
                record, HandoffState.EXPIRED, expected_version, idempotency_key
            )
        if not await self._membership.active_member(
            tenant_id, record.civilization_id, record.target_agent_id
        ):
            raise PermissionError("handoff target membership is no longer active")
        if hashlib.sha256(token.encode()).hexdigest() != record.acceptance_token_digest:
            raise PermissionError("invalid one-time acceptance token")
        if not bool(await self._invoke(self._authorize_target, record)):
            raise PermissionError("target authorization denied")
        return await self._transition(
            record, HandoffState.ACCEPTED, expected_version, idempotency_key
        )

    async def transition(
        self,
        tenant_id: str,
        handoff_id: str,
        *,
        target: HandoffState,
        expected_version: int,
        idempotency_key: str,
        result_reference: str | None = None,
    ) -> HandoffRecord:
        record = await self._required(tenant_id, handoff_id)
        return await self._transition(
            record, target, expected_version, idempotency_key, result_reference
        )

    async def report(
        self,
        tenant_id: str,
        handoff_id: str,
        *,
        target: HandoffState,
        token: str,
        expected_version: int,
        idempotency_key: str,
        result_reference: str | None = None,
    ) -> HandoffRecord:
        """Target-side progress report (executing / completed / failed).

        Only the holder of the one-time acceptance token (the accepting target)
        may report, so another caller in the tenant cannot complete someone
        else's handoff and resume its parent.
        """
        if target not in _REPORTABLE_STATES:
            raise PermissionError(f"handoff state {target.value} cannot be reported by the target")
        record = await self._required(tenant_id, handoff_id)
        supplied = hashlib.sha256(token.encode()).hexdigest()
        if not hmac.compare_digest(supplied, record.acceptance_token_digest):
            raise PermissionError("invalid handoff acceptance token")
        return await self._transition(
            record, target, expected_version, idempotency_key, result_reference
        )

    async def _required(self, tenant_id: str, handoff_id: str) -> HandoffRecord:
        record = await self._repository.get(tenant_id, handoff_id)
        if record is None:
            raise KeyError(handoff_id)
        return record

    async def get(self, tenant_id: str, handoff_id: str) -> HandoffRecord | None:
        return await self._repository.get(tenant_id, handoff_id)

    async def _transition(
        self,
        record: HandoffRecord,
        target: HandoffState,
        expected_version: int,
        idempotency_key: str,
        result_reference: str | None = None,
    ) -> HandoffRecord:
        updated, event, replayed = await self._repository.transition(
            record.tenant_id,
            record.handoff_id,
            target=target,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            result_reference=result_reference,
        )
        if not replayed:
            await self._invoke(
                self._emit,
                event.event_type,
                {
                    "tenant_id": record.tenant_id,
                    "session_id": record.session_id,
                    "handoff_id": record.handoff_id,
                    "version": event.version,
                    "from_state": event.from_state.value,
                    "to_state": event.to_state.value,
                    "result_reference": updated.result_reference,
                },
            )
        if event.to_state is HandoffState.ACCEPTED and record.handoff_id not in self._paused:
            await self._invoke(self._pause, updated)
            self._paused.add(record.handoff_id)
        if (
            event.to_state in _PARENT_RELEASING_STATES
            and event.from_state in _DELEGATED_STATES
            and record.handoff_id not in self._resumed
        ):
            # Also on a replayed command: a first attempt that failed after the
            # transition committed (or ran on another replica) must still resume.
            await self._invoke(self._resume, updated)
            self._resumed.add(record.handoff_id)
        return updated


# Once accepted, the parent waits; any end of the delegated work releases it.
_DELEGATED_STATES = frozenset({HandoffState.ACCEPTED, HandoffState.EXECUTING})
_PARENT_RELEASING_STATES = frozenset(
    {
        HandoffState.COMPLETED,
        HandoffState.FAILED,
        HandoffState.CANCELLED,
        HandoffState.EXPIRED,
    }
)
_REPORTABLE_STATES = frozenset(
    {HandoffState.EXECUTING, HandoffState.COMPLETED, HandoffState.FAILED}
)

__all__ = ["HandoffMembership", "HandoffService"]
