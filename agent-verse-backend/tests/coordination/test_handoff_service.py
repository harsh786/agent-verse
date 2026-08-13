from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.coordination.handoffs.models import HandoffState
from app.coordination.handoffs.repository import InMemoryHandoffRepository
from app.coordination.handoffs.service import HandoffService


class Membership:
    def __init__(self, members: set[str] | None = None) -> None:
        self.members = members or {"source", "target"}

    async def active_member(self, _tenant: str, _civilization: str, agent: str) -> bool:
        return agent in self.members

    async def connector_allowlist(self, _tenant: str, _civilization: str, agent: str):
        return frozenset({"shared", agent})


@pytest.mark.asyncio
async def test_handoff_lifecycle_intersects_tools_and_resumes_parent_once() -> None:
    events: list[str] = []
    resumed: list[str] = []
    service = HandoffService(
        InMemoryHandoffRepository(),
        membership=Membership(),
        emit_event=lambda event, _payload: events.append(event),
        resume_parent=lambda record: resumed.append(record.handoff_id),
    )
    requested = await service.request(
        tenant_id="tenant",
        session_id="session",
        civilization_id="civilization",
        source_agent_id="source",
        target_agent_id="target",
        task_summary="Perform bounded task",
        remaining_budget_usd=1.0,
        deadline=datetime.now(UTC) + timedelta(minutes=5),
        acceptance_token="token",
        idempotency_key="request",
    )
    assert requested.connector_allowlist == frozenset({"shared"})
    accepted = await service.accept(
        "tenant", requested.handoff_id, token="token", expected_version=1, idempotency_key="accept"
    )
    executing = await service.transition(
        "tenant", requested.handoff_id, target=HandoffState.EXECUTING,
        expected_version=accepted.version, idempotency_key="execute"
    )
    completed = await service.transition(
        "tenant", requested.handoff_id, target=HandoffState.COMPLETED,
        expected_version=executing.version, idempotency_key="complete", result_reference="result://1"
    )
    duplicate = await service.transition(
        "tenant", requested.handoff_id, target=HandoffState.COMPLETED,
        expected_version=executing.version, idempotency_key="complete", result_reference="result://1"
    )
    assert duplicate == completed
    assert resumed == [requested.handoff_id]
    assert events == [
        "handoff.requested.v1", "handoff.accepted.v1", "handoff.executing.v1",
        "handoff.completed.v1",
    ]


@pytest.mark.asyncio
async def test_duplicate_request_and_transition_emit_each_event_once() -> None:
    events: list[str] = []
    service = HandoffService(
        InMemoryHandoffRepository(),
        membership=Membership(),
        emit_event=lambda event, _payload: events.append(event),
    )
    request = {
        "tenant_id": "tenant",
        "session_id": "session",
        "civilization_id": "civilization",
        "source_agent_id": "source",
        "target_agent_id": "target",
        "task_summary": "Perform bounded task",
        "remaining_budget_usd": 1.0,
        "deadline": datetime.now(UTC) + timedelta(minutes=5),
        "acceptance_token": "token",
        "idempotency_key": "request-once",
    }
    first = await service.request(**request)
    duplicate = await service.request(**request)
    assert duplicate == first
    accepted = await service.accept(
        "tenant", first.handoff_id, token="token", expected_version=1,
        idempotency_key="accept-once",
    )
    replay = await service.accept(
        "tenant", first.handoff_id, token="token", expected_version=1,
        idempotency_key="accept-once",
    )
    assert replay == accepted
    assert events == ["handoff.requested.v1", "handoff.accepted.v1"]


@pytest.mark.asyncio
async def test_nonmember_bad_token_and_membership_change_fail_closed() -> None:
    membership = Membership()
    service = HandoffService(InMemoryHandoffRepository(), membership=membership)
    with pytest.raises(PermissionError, match="active"):
        await service.request(
            tenant_id="tenant", session_id="session", civilization_id="civilization",
            source_agent_id="source", target_agent_id="outsider", task_summary="task",
            remaining_budget_usd=1, deadline=datetime.now(UTC) + timedelta(minutes=1),
            acceptance_token="token", idempotency_key="bad"
        )
    requested = await service.request(
        tenant_id="tenant", session_id="session", civilization_id="civilization",
        source_agent_id="source", target_agent_id="target", task_summary="task",
        remaining_budget_usd=1, deadline=datetime.now(UTC) + timedelta(minutes=1),
        acceptance_token="token", idempotency_key="ok"
    )
    with pytest.raises(PermissionError, match="token"):
        await service.accept(
            "tenant", requested.handoff_id, token="wrong", expected_version=1,
            idempotency_key="accept-bad"
        )
    membership.members.remove("target")
    with pytest.raises(PermissionError, match="membership"):
        await service.accept(
            "tenant", requested.handoff_id, token="token", expected_version=1,
            idempotency_key="accept-after-change"
        )
