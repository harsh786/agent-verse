"""WF-41 on real Postgres (RLS-enforced app role): decide vs delegate race.

A decision and a delegation of the same approval run concurrently, many times
over; the approval always ends decided, exactly one decision wins, and the
delegation either landed first (still visible in the discussion) or was refused.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid

import pytest

from app.workflow.approval_store import PostgresWorkflowApprovalStore
from app.workflow.hitl_extension import WorkflowHITLRequest
from tests.workflow.test_approval_store import _req, postgres_url, store  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def test_decide_and_delegate_in_parallel_never_revert(
    store: PostgresWorkflowApprovalStore,  # noqa: F811
) -> None:
    tenant = str(uuid.uuid4())
    for _ in range(20):
        req = _req(tenant)
        await store.save(req)
        decided = dataclasses.replace(
            req, status="decided", action_taken="approve", reviewed_by="alice"
        )
        entry = {"type": "delegation", "from": "alice", "to": "bob"}
        won, mutated = await asyncio.gather(
            store.decide_if_pending(decided),
            store.mutate_if_pending(
                req.request_id, tenant, discussion_entry=entry, assignment={"assigned_to": "bob"}
            ),
        )
        final = await store.get(req.request_id, tenant)
        assert final is not None
        assert won is True
        assert final.status == "decided" and final.action_taken == "approve"
        if mutated is not None:  # the delegation committed first
            assert isinstance(mutated, WorkflowHITLRequest) and mutated.status == "pending"


async def test_mutation_of_decided_approval_returns_none(
    store: PostgresWorkflowApprovalStore,  # noqa: F811
) -> None:
    tenant = str(uuid.uuid4())
    req = _req(tenant)
    await store.save(req)
    assert await store.decide_if_pending(dataclasses.replace(req, status="decided"))
    assert (
        await store.mutate_if_pending(
            req.request_id, tenant, discussion_entry={"type": "comment", "text": "late"}
        )
        is None
    )
    final = await store.get(req.request_id, tenant)
    assert final is not None and final.status == "decided" and final.discussion == []


async def test_mutation_appends_discussion_and_reassigns(
    store: PostgresWorkflowApprovalStore,  # noqa: F811
) -> None:
    tenant = str(uuid.uuid4())
    req = _req(tenant)
    await store.save(req)
    out = await store.mutate_if_pending(
        req.request_id,
        tenant,
        discussion_entry={"type": "escalation", "by": "sla"},
        assignment={"assigned_to": None, "assigned_role": "managers"},
    )
    assert out is not None
    assert out.assigned_to is None and out.assigned_role == "managers"
    assert out.discussion == [{"type": "escalation", "by": "sla"}]
    pending, _ = await store.list_pending(tenant)
    assert req.request_id in {r.request_id for r in pending}
