from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.coordination.contracts import AuthorizationContext
from app.coordination.service import CoordinationService, SessionAdmission
from app.coordination.store import AcceptedTransition, CoordinationSessionRecord
from app.tenancy.context import PlanTier, TenantContext


@dataclass
class Store:
    created: dict[str, object] | None = None

    async def create_session(self, tenant_ctx: TenantContext, **values: object):
        self.created = {"tenant_id": tenant_ctx.tenant_id, **values}
        return CoordinationSessionRecord(
            session_id="session-1",
            tenant_id=tenant_ctx.tenant_id,
            state="pending",
            next_sequence=1,
            version=1,
        )

    async def transition_session(self, tenant_ctx: TenantContext, **values: object):
        del tenant_ctx
        return AcceptedTransition(
            event_id="event-1",
            session_id=str(values["session_id"]),
            sequence=1,
            state=str(values["target_state"]),
            version=int(values["expected_version"]) + 1,
            idempotency_key=str(values["idempotency_key"]),
        )


CONTEXT = TenantContext(
    tenant_id="tenant-1", plan=PlanTier.FREE, api_key_id="key-1"
)


async def test_admission_freezes_policy_and_budget_snapshots() -> None:
    store = Store()
    policy = {"version": "p1", "tools": ["read"]}
    budget = {"ceiling": 5}
    admission = SessionAdmission(
        civilization_id="civ-1",
        goal_id="goal-1",
        policy_snapshot=policy,
        budget_snapshot=budget,
        authorization=AuthorizationContext(
            actor_id="user-1", permissions=frozenset({"coordination:create"})
        ),
    )

    await CoordinationService(store).create_session(CONTEXT, admission)
    policy["tools"].append("delete")
    budget["ceiling"] = 100

    assert store.created is not None
    assert store.created["policy_snapshot"] == {"version": "p1", "tools": ["read"]}
    assert store.created["budget_snapshot"] == {"ceiling": 5}


async def test_admission_requires_explicit_authority() -> None:
    admission = SessionAdmission(
        civilization_id="civ-1",
        goal_id="goal-1",
        policy_snapshot={},
        budget_snapshot={},
        authorization=AuthorizationContext(
            actor_id="user-1", permissions=frozenset()
        ),
    )

    with pytest.raises(PermissionError, match="coordination:create"):
        await CoordinationService(Store()).create_session(CONTEXT, admission)


async def test_start_and_complete_use_versioned_idempotent_transitions() -> None:
    service = CoordinationService(Store())

    started = await service.start_session(
        CONTEXT, "session-1", expected_version=1, idempotency_key="start-1"
    )
    completed = await service.complete_session(
        CONTEXT, "session-1", expected_version=2, idempotency_key="finish-1"
    )

    assert started.state == "active"
    assert completed.state == "completed"
    assert completed.version == 3
