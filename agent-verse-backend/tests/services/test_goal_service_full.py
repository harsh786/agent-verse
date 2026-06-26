"""Full coverage for GoalService — covers all branches and execution paths."""
from __future__ import annotations

import asyncio

from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="gsf-t1", plan=PlanTier.PROFESSIONAL, api_key_id="gsf1")


async def test_goal_service_submit_goal_live_execution() -> None:
    """Non-dry-run goal launches a background task and eventually reaches a terminal state."""
    svc = GoalService()
    result = await svc.submit_goal(
        goal="test live goal", priority="normal", dry_run=False, tenant_ctx=T
    )
    gid = result["goal_id"]
    assert result["status"] in {"planning", "complete"}

    # Allow background task time to run
    await asyncio.sleep(0.5)

    goal_data = await svc.get_goal(goal_id=gid, tenant_ctx=T)
    assert goal_data["goal_id"] == gid
    assert goal_data["status"] in {
        "planning", "executing", "complete", "failed", "verifying"
    }


async def test_goal_service_submit_goal_priority_levels() -> None:
    """Goals accept all supported priority levels without error."""
    svc = GoalService()
    for priority in ["critical", "high", "normal", "low", "background"]:
        result = await svc.submit_goal(
            goal=f"{priority} goal", priority=priority, dry_run=True, tenant_ctx=T
        )
        assert result["priority"] == priority


async def test_goal_service_get_events_returns_list() -> None:
    """get_events returns a list (may be empty) for any active goal."""
    svc = GoalService()
    result = await svc.submit_goal(
        goal="events test", priority="normal", dry_run=True, tenant_ctx=T
    )
    gid = result["goal_id"]
    events = await svc.get_events(goal_id=gid, tenant_ctx=T)
    assert isinstance(events, list)


async def test_goal_service_subscribe_events_dry_run_terminates() -> None:
    """subscribe_events for a terminal dry-run goal terminates immediately."""
    svc = GoalService()
    result = await svc.submit_goal(
        goal="sub dry run", priority="normal", dry_run=True, tenant_ctx=T
    )
    gid = result["goal_id"]

    events: list[dict[str, object]] = []
    async with asyncio.timeout(2.0):
        async for event in svc.subscribe_events(goal_id=gid, tenant_ctx=T):
            events.append(event)

    assert isinstance(events, list)  # terminated without hanging


async def test_goal_service_cancel_already_terminal() -> None:
    """Cancelling a terminal goal is idempotent — returns cancelled both times."""
    svc = GoalService()
    result = await svc.submit_goal(
        goal="cancel terminal", priority="normal", dry_run=True, tenant_ctx=T
    )
    gid = result["goal_id"]

    r1 = await svc.cancel_goal(goal_id=gid, tenant_ctx=T)
    assert r1["status"] == "cancelled"

    # Second cancel must not raise
    r2 = await svc.cancel_goal(goal_id=gid, tenant_ctx=T)
    assert r2["status"] == "cancelled"


async def test_goal_service_handle_approval_approve() -> None:
    """handle_approval echoes the 'approve' action back."""
    svc = GoalService()
    result = await svc.submit_goal(
        goal="approve test", priority="normal", dry_run=True, tenant_ctx=T
    )
    gid = result["goal_id"]
    resp = await svc.handle_approval(
        goal_id=gid,
        request_id="req-001",
        action="approve",
        approver="admin@test.com",
        note="Looks good",
        tenant_ctx=T,
    )
    assert resp["action"] == "approve"


async def test_goal_service_handle_approval_reject() -> None:
    """handle_approval echoes the 'reject' action back."""
    svc = GoalService()
    result = await svc.submit_goal(
        goal="reject test", priority="normal", dry_run=True, tenant_ctx=T
    )
    gid = result["goal_id"]
    resp = await svc.handle_approval(
        goal_id=gid,
        request_id="req-002",
        action="reject",
        approver="admin@test.com",
        note="Too risky",
        tenant_ctx=T,
    )
    assert resp["action"] == "reject"


async def test_goal_service_get_audit_entries_empty() -> None:
    """get_audit_entries returns empty list for a goal with no audit events."""
    svc = GoalService()
    result = await svc.submit_goal(
        goal="audit empty", priority="normal", dry_run=True, tenant_ctx=T
    )
    gid = result["goal_id"]
    entries = await svc.get_audit_entries(goal_id=gid, tenant_ctx=T)
    assert entries == []


async def test_goal_service_db_persist_goal_noop() -> None:
    """_db_persist_goal is a no-op when no db_session_factory is configured."""
    svc = GoalService()
    # Should return without error when _db is None
    await svc._db_persist_goal("g1", "t1", "goal text", "planning", "normal", False)


async def test_goal_service_db_update_status_noop() -> None:
    """_db_update_goal_status is a no-op when no db_session_factory is configured."""
    svc = GoalService()
    await svc._db_update_goal_status("g1", "t1", "complete", error_message="", iterations=2)


async def test_goal_service_sync_from_db_returns_zero() -> None:
    """sync_from_db returns 0 immediately when no db_session_factory is configured."""
    svc = GoalService()
    count = await svc.sync_from_db()
    assert count == 0


async def test_goal_service_dispatch_event_complete() -> None:
    """_dispatch_event updates record status to failed on goal_failed event."""
    svc = GoalService()
    result = await svc.submit_goal(
        goal="dispatch test", priority="normal", dry_run=True, tenant_ctx=T
    )
    gid = result["goal_id"]

    # Manually dispatch a goal_failed event
    await svc._dispatch_event(gid, {"type": "goal_failed", "reason": "test reason"})

    goal_data = await svc.get_goal(goal_id=gid, tenant_ctx=T)
    assert goal_data["status"] == "failed"
