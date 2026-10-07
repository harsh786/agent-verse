"""WF-41: delegate / escalate / comment can never revert a decided approval.

They upserted a stale full copy of the approval (status included) and escalate
never checked the status, so racing a decision flipped it back to ``pending``,
allowing a second decision and a second resume. They are now conditional
updates on ``status = 'pending'`` that never write the status; a lost race is a
409.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any

import pytest

from app.workflow.hitl_extension import (
    ApprovalNotPendingError,
    HITLWorkflowGateway,
    WorkflowHITLRequest,
)

_T = "11111111-1111-1111-1111-111111111111"


class _AtomicStore:
    """Stores JSON like Postgres; conditional updates interleave but stay atomic."""

    def __init__(self) -> None:
        self.rows: dict[str, str] = {}

    async def save(self, req: WorkflowHITLRequest) -> None:
        self.rows[req.request_id] = json.dumps(dataclasses.asdict(req))

    async def get(self, request_id: str, tenant_id: str | None = None) -> Any:
        raw = self.rows.get(request_id)
        return WorkflowHITLRequest(**json.loads(raw)) if raw else None

    async def decide_if_pending(self, req: WorkflowHITLRequest) -> bool:
        await asyncio.sleep(0.005)
        current = json.loads(self.rows[req.request_id])
        if current["status"] != "pending":
            return False
        self.rows[req.request_id] = json.dumps(dataclasses.asdict(req))
        return True

    async def mutate_if_pending(
        self,
        request_id: str,
        tenant_id: str,
        *,
        discussion_entry: Any,
        assignment: Any = None,
        escalated_at: str | None = None,
    ) -> Any:
        await asyncio.sleep(0.005)
        current = json.loads(self.rows[request_id])
        if current["status"] != "pending":
            return None
        current.update(assignment or {})
        if escalated_at:
            current["escalated_at"] = escalated_at
            current["escalation_level"] = int(current.get("escalation_level") or 0) + 1
        current["discussion"] = [*current.get("discussion", []), discussion_entry]
        self.rows[request_id] = json.dumps(current)
        return WorkflowHITLRequest(**current)


async def _gateway() -> tuple[HITLWorkflowGateway, list[str], str, _AtomicStore]:
    resumed: list[str] = []

    async def resume(req: Any) -> None:
        resumed.append(req.action_taken)

    store = _AtomicStore()
    gw = HITLWorkflowGateway(approval_store=store, resume_callback=resume)
    rid = await gw.create_workflow_approval(
        run_id="r", step_id="gate", tenant_id=_T, strategy="specific", specific_user="alice",
        escalation_to_role="managers",
    )
    return gw, resumed, rid, store


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["delegate", "escalate", "comment"])
async def test_racing_a_decision_never_reverts_it(op: str) -> None:
    gw, resumed, rid, store = await _gateway()

    async def mutate() -> Any:
        if op == "delegate":
            return await gw.delegate(rid, "alice", "bob", tenant_id=_T)
        if op == "escalate":
            return await gw.escalate(rid, "alice", tenant_id=_T)
        return await gw.add_comment(rid, "alice", "looks fine", tenant_id=_T)

    results = await asyncio.gather(
        gw.decide(rid, "approve", "alice", tenant_id=_T), mutate(), return_exceptions=True
    )
    final = await store.get(rid, _T)
    # "approve" leaves status "approved" (WF-APPROVAL-WORKFLOW-ID); never "pending".
    assert final.status == "approved" and final.action_taken == "approve", (final, results)
    assert resumed == ["approve"]
    # A second decision is refused: the approval stayed decided.
    with pytest.raises(Exception, match=r"(?i)already decided"):
        await gw.decide(rid, "reject", "bob", tenant_id=_T)
    assert resumed == ["approve"]


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["delegate", "escalate", "comment"])
async def test_mutating_a_decided_approval_is_refused(op: str) -> None:
    gw, _resumed, rid, store = await _gateway()
    await gw.decide(rid, "approve", "alice", tenant_id=_T)
    with pytest.raises(ApprovalNotPendingError):
        if op == "delegate":
            await gw.delegate(rid, "alice", "bob", tenant_id=_T)
        elif op == "escalate":
            await gw.escalate(rid, "alice", tenant_id=_T)
        else:
            await gw.add_comment(rid, "alice", "x", tenant_id=_T)
    assert (await store.get(rid, _T)).status == "approved"


@pytest.mark.asyncio
async def test_escalate_reassigns_to_the_role_on_a_pending_approval() -> None:
    gw, _resumed, rid, store = await _gateway()
    out = await gw.escalate(rid, "sla", tenant_id=_T)
    assert out.assigned_role == "managers" and out.assigned_to is None
    stored = await store.get(rid, _T)
    assert stored.status == "pending" and stored.discussion[-1]["type"] == "escalation"
    # An escalation is visible as such (time + level), not only in the thread.
    assert stored.escalated_at and stored.escalation_level == 1
