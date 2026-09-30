"""ORG-22: a finished handoff resumes the delegating session and emits an event."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.coordination.contracts import AuthorizationContext
from app.coordination.handoffs.models import HandoffState
from app.coordination.handoffs.repository import InMemoryHandoffRepository
from app.coordination.handoffs.resumption import HandoffParentResumer
from app.coordination.handoffs.service import HandoffService
from app.coordination.live_bus import CoordinationLiveBus
from app.coordination.service import CoordinationService, SessionAdmission
from app.coordination.store import InMemoryCoordinationStore
from app.coordination.transcript.repository import InMemoryTranscriptRepository
from app.coordination.transcript.service import TranscriptService
from app.tenancy.context import PlanTier, TenantContext

_TENANT = TenantContext(tenant_id="tenant", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class Membership:
    async def active_member(self, _tenant: str, _civilization: str, agent: str) -> bool:
        return agent in {"source", "target"}

    async def connector_allowlist(
        self, _tenant: str, _civilization: str, agent: str
    ) -> frozenset[str]:
        return frozenset({"shared", agent})


class Runtime:
    def __init__(self) -> None:
        self.coordination = CoordinationService(InMemoryCoordinationStore())
        self.transcript = TranscriptService(InMemoryTranscriptRepository())
        self.bus = CoordinationLiveBus()
        self.repository = InMemoryHandoffRepository()

    def service(self, resumer: HandoffParentResumer | None = None) -> HandoffService:
        resumer = resumer or self.resumer()
        return HandoffService(
            self.repository,
            membership=Membership(),
            emit_event=resumer.emit_event,
            pause_parent=resumer.pause_parent,
            resume_parent=resumer.resume_parent,
        )

    def resumer(self) -> HandoffParentResumer:
        return HandoffParentResumer(
            coordination_service=lambda: self.coordination,
            transcript_service=lambda: self.transcript,
            live_bus=lambda: self.bus,
        )

    async def active_session(self) -> str:
        created = await self.coordination.create_session(
            _TENANT,
            SessionAdmission(
                civilization_id="civilization",
                goal_id="goal",
                policy_snapshot={},
                budget_snapshot={},
                authorization=AuthorizationContext(
                    actor_id="k", permissions=frozenset({"coordination:create"})
                ),
            ),
        )
        await self.coordination.start_session(
            _TENANT, created.session_id, expected_version=1, idempotency_key="start"
        )
        return created.session_id


async def _request(service: HandoffService, session_id: str, key: str = "request") -> Any:
    return await service.request(
        tenant_id="tenant",
        session_id=session_id,
        civilization_id="civilization",
        source_agent_id="source",
        target_agent_id="target",
        task_summary="Draft the report",
        remaining_budget_usd=1.0,
        deadline=datetime.now(UTC) + timedelta(minutes=5),
        acceptance_token="token-token-token",
        idempotency_key=key,
    )


async def _drain(frames: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    while True:
        try:
            out.append(await asyncio.wait_for(anext(frames), 0.05))
        except TimeoutError:
            return out


@pytest.mark.asyncio
async def test_completing_a_handoff_resumes_parent_and_emits_event() -> None:
    runtime = Runtime()
    session_id = await runtime.active_session()
    service = runtime.service()
    async with runtime.bus.subscribe("tenant", session_id) as frames:
        requested = await _request(service, session_id)
        accepted = await service.accept(
            "tenant", requested.handoff_id, token="token-token-token",
            expected_version=1, idempotency_key="accept",
        )
        # The delegating session waits while the target works.
        assert (await runtime.coordination.get_session(_TENANT, session_id)).state == "paused"
        executing = await service.transition(
            "tenant", requested.handoff_id, target=HandoffState.EXECUTING,
            expected_version=accepted.version, idempotency_key="execute",
        )
        await service.transition(
            "tenant", requested.handoff_id, target=HandoffState.COMPLETED,
            expected_version=executing.version, idempotency_key="complete",
            result_reference="artifact://report",
        )
        emitted = await _drain(frames)

    assert (await runtime.coordination.get_session(_TENANT, session_id)).state == "active"
    transcript = await runtime.transcript.page("tenant", session_id)
    resume = [m for m in transcript if m.message_type == "decision"]
    assert len(resume) == 1
    assert resume[0].sender_agent_id == "target"
    assert resume[0].recipient_agent_ids == ("source",)
    assert "completed" in (resume[0].safe_content or "")
    assert "parent resumed" in (resume[0].safe_content or "")
    event_types = [f.get("event_type") for f in emitted if f.get("type") == "event"]
    assert "handoff.completed.v1" in event_types
    assert "handoff.parent_resumed.v1" in event_types
    assert any(
        f.get("type") == "message" and f["message"]["message_id"] == resume[0].message_id
        for f in emitted
    )


@pytest.mark.asyncio
async def test_resume_survives_a_failed_first_attempt_on_another_replica() -> None:
    runtime = Runtime()
    session_id = await runtime.active_session()
    flaky = runtime.resumer()
    calls = {"n": 0}
    original = flaky.resume_parent

    async def fail_once(record: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transcript store unavailable")
        await original(record)

    flaky.resume_parent = fail_once  # type: ignore[method-assign]
    first_replica = runtime.service(flaky)
    requested = await _request(first_replica, session_id)
    accepted = await first_replica.accept(
        "tenant", requested.handoff_id, token="token-token-token",
        expected_version=1, idempotency_key="accept",
    )
    executing = await first_replica.transition(
        "tenant", requested.handoff_id, target=HandoffState.EXECUTING,
        expected_version=accepted.version, idempotency_key="execute",
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        await first_replica.transition(
            "tenant", requested.handoff_id, target=HandoffState.COMPLETED,
            expected_version=executing.version, idempotency_key="complete",
        )
    # The client retries the same command against another replica: the completion
    # is a replay, but the parent is still resumed (idempotently).
    second_replica = runtime.service()
    await second_replica.transition(
        "tenant", requested.handoff_id, target=HandoffState.COMPLETED,
        expected_version=executing.version, idempotency_key="complete",
    )
    assert (await runtime.coordination.get_session(_TENANT, session_id)).state == "active"
    transcript = await runtime.transcript.page("tenant", session_id)
    assert len([m for m in transcript if m.message_type == "decision"]) == 1


@pytest.mark.asyncio
async def test_cancelled_handoff_also_releases_the_parent() -> None:
    runtime = Runtime()
    session_id = await runtime.active_session()
    service = runtime.service()
    requested = await _request(service, session_id)
    accepted = await service.accept(
        "tenant", requested.handoff_id, token="token-token-token",
        expected_version=1, idempotency_key="accept",
    )
    await service.transition(
        "tenant", requested.handoff_id, target=HandoffState.CANCELLED,
        expected_version=accepted.version, idempotency_key="cancel",
    )
    assert (await runtime.coordination.get_session(_TENANT, session_id)).state == "active"
    transcript = await runtime.transcript.page("tenant", session_id)
    assert "cancelled" in (transcript[-1].safe_content or "")


@pytest.mark.asyncio
async def test_completion_token_is_required_to_report_results() -> None:
    runtime = Runtime()
    session_id = await runtime.active_session()
    service = runtime.service()
    requested = await _request(service, session_id)
    accepted = await service.accept(
        "tenant", requested.handoff_id, token="token-token-token",
        expected_version=1, idempotency_key="accept",
    )
    with pytest.raises(PermissionError, match="token"):
        await service.report(
            "tenant", requested.handoff_id, target=HandoffState.EXECUTING,
            token="wrong-wrong-wrong", expected_version=accepted.version,
            idempotency_key="execute",
        )
    with pytest.raises(PermissionError, match="report"):
        await service.report(
            "tenant", requested.handoff_id, target=HandoffState.CANCELLED,
            token="token-token-token", expected_version=accepted.version,
            idempotency_key="cancel",
        )
    executing = await service.report(
        "tenant", requested.handoff_id, target=HandoffState.EXECUTING,
        token="token-token-token", expected_version=accepted.version,
        idempotency_key="execute",
    )
    assert executing.state is HandoffState.EXECUTING


def test_app_wires_the_resumer_into_the_handoff_service() -> None:
    from app.main import create_app

    app = create_app(manage_pools=False)
    service = app.state.handoff_service
    for callback in (service._resume, service._pause, service._emit):
        assert isinstance(getattr(callback, "__self__", None), HandoffParentResumer)
