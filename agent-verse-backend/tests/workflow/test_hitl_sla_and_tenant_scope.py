"""Regression: workflow HITL escalation never ran and delegate/escalate ignored
the caller's tenant.

* ``check_and_escalate_overdue`` did not exist, so the beat task was a no-op;
* ``delegate`` / ``escalate`` looked requests up without a tenant, so on the
  Redis / in-memory mirror a request id from another tenant resolved.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.workflow.hitl_extension import HITLWorkflowGateway, WorkflowHITLRequest


def _req(rid: str, tenant: str = "t1", **kw: object) -> WorkflowHITLRequest:
    return WorkflowHITLRequest(
        request_id=rid, tenant_id=tenant, run_id="r", workflow_id="w", **kw  # type: ignore[arg-type]
    )


async def _gw(*reqs: WorkflowHITLRequest) -> HITLWorkflowGateway:
    gw = HITLWorkflowGateway()
    for r in reqs:
        await gw._save(r)
    return gw


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


async def test_overdue_deadline_escalates_once_to_the_escalation_role() -> None:
    req = _req(
        "a",
        deadline_at=(NOW - timedelta(minutes=1)).isoformat(),
        escalation_to_role="manager",
        assigned_to="ada",
    )
    gw = await _gw(req)
    first = await gw.check_and_escalate_overdue(now=NOW)
    second = await gw.check_and_escalate_overdue(now=NOW)
    assert first["escalated"] == 1 and second["escalated"] == 0
    stored = await gw.get_request("a", "t1")
    assert stored is not None and stored.assigned_role == "manager" and stored.assigned_to is None


async def test_not_yet_due_is_left_alone_and_other_actions_are_applied() -> None:
    """auto_approve used to be counted as ``skipped`` (NOT IMPLEMENTED); every
    timeout action is applied now (tests/workflow/test_hitl_timeout_actions.py)."""
    future = _req("f", deadline_at=(NOW + timedelta(hours=1)).isoformat())
    auto = _req("g", deadline_at=(NOW - timedelta(hours=1)).isoformat(), timeout_action="auto_approve")
    gw = await _gw(future, auto)
    result = await gw.check_and_escalate_overdue(now=NOW)
    assert result["escalated"] == 0 and result["auto_approved"] == 1
    assert result["skipped"] == 0 and result["failed"] == 0
    still = await gw.get_request("f", "t1")
    assert still is not None and still.status == "pending" and still.timed_out_at is None


async def test_age_based_escalation_without_deadline() -> None:
    old = _req("o", created_at=(NOW - timedelta(hours=49)).isoformat())
    gw = await _gw(old)
    assert (await gw.check_and_escalate_overdue(now=NOW))["escalated"] == 1


async def test_delegate_and_escalate_are_tenant_scoped() -> None:
    gw = await _gw(_req("x", tenant="tenant-a"))
    with pytest.raises(ValueError):
        await gw.delegate("x", "eve", "mallory", tenant_id="tenant-b")
    with pytest.raises(ValueError):
        await gw.escalate("x", "eve", tenant_id="tenant-b")
    ok = await gw.delegate("x", "ada", "bob", tenant_id="tenant-a")
    assert ok.assigned_to == "bob"
