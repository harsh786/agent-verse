"""Regression: org-scoped approval-center endpoints (G-24) are DB-task-backed.

Approval gates are durable OrgTasks (task_kind='approval_gate'). The endpoints
operate on the task id — approve flips it to 'running' and, when it was the last
pending gate, launches the mission's deferred goal; reject cancels it and fails
the guarded mission. They are org-scoped (a team-lead of one org cannot action
another org's gate by id) and 404 when the gate is genuinely absent.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.org.router import _OrgApprovalDecision, approve_org_request, reject_org_request


class _Task:
    def __init__(self, tid: str, org_id: str, status: str = "approval_required") -> None:
        self.id = tid
        self.org_id = org_id
        self.mission_id = "m1"
        self.status = status
        self.outputs: list = []
        self.extra_data = {"task_kind": "approval_gate", "gate": {"type": "financial"}}


class _MockService:
    def __init__(self, task: _Task | None) -> None:
        self._tenant_id = "tenant-1"
        self._task = task
        self.dispatched = False
        self.mission_status: str | None = None

    async def get_task(self, tid: str):
        return self._task if (self._task and str(self._task.id) == tid) else None

    async def update_task_status(self, tid: str, status: str, *, outputs=None, **kw):
        if self._task:
            self._task.status = status
        return self._task

    async def list_tasks(self, org_id: str, *, mission_id=None, **kw):
        return [self._task] if self._task else []

    async def dispatch_mission_goal(self, mid: str, *, app_state=None, tenant_ctx=None):
        self.dispatched = True
        return {"dispatched": True, "goal_id": "g1"}

    async def get_mission(self, mid: str):
        return SimpleNamespace(id=mid, status="review")

    async def update_mission_status(self, mid: str, status: str):
        self.mission_status = status
        return SimpleNamespace(id=mid, status=status)


def _request() -> SimpleNamespace:
    state = SimpleNamespace(hitl_gateway=None, goal_service=None)
    return SimpleNamespace(app=SimpleNamespace(state=state))


@pytest.mark.asyncio
async def test_approve_gate_transitions_task_and_launches_deferred_goal() -> None:
    task = _Task("t1", "org-1")
    svc = _MockService(task)

    result = await approve_org_request(
        org_id="org-1",
        approval_id="t1",
        body=_OrgApprovalDecision(approver="alice", notes="ok"),
        request=_request(),
        x_request_id="req-1",
        service=svc,
    )

    assert result["status"] == "approved"
    assert task.status == "running"
    # Last pending gate cleared → the paused mission's goal is dispatched.
    assert svc.dispatched is True
    assert result["mission_dispatched"] is True


@pytest.mark.asyncio
async def test_reject_gate_cancels_task_and_fails_mission() -> None:
    task = _Task("t2", "org-1")
    svc = _MockService(task)

    result = await reject_org_request(
        org_id="org-1",
        approval_id="t2",
        body=_OrgApprovalDecision(approver="bob", notes="no"),
        request=_request(),
        x_request_id="req-2",
        service=svc,
    )

    assert result["status"] == "rejected"
    assert task.status == "cancelled"
    assert svc.mission_status == "failed"


@pytest.mark.asyncio
async def test_approve_cross_org_gate_is_404() -> None:
    # Task belongs to a DIFFERENT org than the URL — must not be actionable.
    svc = _MockService(_Task("t3", "OTHER-ORG"))
    with pytest.raises(HTTPException) as exc:
        await approve_org_request(
            org_id="org-1",
            approval_id="t3",
            body=_OrgApprovalDecision(approver="alice"),
            request=_request(),
            x_request_id="req-3",
            service=svc,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_approve_unknown_gate_is_404() -> None:
    svc = _MockService(None)
    with pytest.raises(HTTPException) as exc:
        await approve_org_request(
            org_id="org-1",
            approval_id="does-not-exist",
            body=_OrgApprovalDecision(approver="alice"),
            request=_request(),
            x_request_id="req-4",
            service=svc,
        )
    assert exc.value.status_code == 404
