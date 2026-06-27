"""Tests for GoalService memory management and limits."""
import asyncio

import pytest

from app.agent.state import GoalStatus
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="mem-t1", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _make_terminal_record(
    goal_id: str, tenant_id: str, status: GoalStatus = GoalStatus.COMPLETE
) -> GoalRecord:
    record = GoalRecord(
        goal_id=goal_id,
        goal_text="test",
        status=status,
        tenant_id=tenant_id,
        priority="normal",
        dry_run=False,
        created_at="2024-01-01T00:00:00Z",
    )
    record.completed_at = "2020-01-01T00:00:00Z"  # very old — well past TTL
    return record


def test_evict_stale_goals_removes_old_terminal():
    svc = GoalService()
    # Add 3 old terminal goals and 1 running goal
    svc._goals["g1"] = _make_terminal_record("g1", T.tenant_id)
    svc._goals["g2"] = _make_terminal_record("g2", T.tenant_id, GoalStatus.FAILED)
    svc._goals["g3"] = _make_terminal_record("g3", T.tenant_id, GoalStatus.CANCELLED)
    svc._goals["g4"] = GoalRecord(
        goal_id="g4",
        goal_text="running",
        status=GoalStatus.EXECUTING,
        tenant_id=T.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="",
    )
    evicted = svc._evict_stale_goals()
    assert evicted >= 3  # The 3 old terminal goals evicted
    assert "g4" in svc._goals  # Running goal kept
    assert "g1" not in svc._goals
    assert "g2" not in svc._goals
    assert "g3" not in svc._goals


def test_sweep_pause_events_removes_orphans():
    from app.services.goal_service import _GOAL_PAUSE_EVENTS

    _GOAL_PAUSE_EVENTS["orphan-goal"] = asyncio.Event()
    svc = GoalService()
    # orphan-goal is not in _goals
    svc._sweep_pause_events()
    assert "orphan-goal" not in _GOAL_PAUSE_EVENTS


def test_success_rate_uses_terminal_denominator():
    svc = GoalService()
    # Add 5 complete + 5 failed + 5 running
    for i in range(5):
        svc._goals[f"c{i}"] = _make_terminal_record(f"c{i}", T.tenant_id, GoalStatus.COMPLETE)
    for i in range(5):
        svc._goals[f"f{i}"] = _make_terminal_record(f"f{i}", T.tenant_id, GoalStatus.FAILED)
    for i in range(5):
        r = GoalRecord(
            goal_id=f"r{i}",
            goal_text="running",
            status=GoalStatus.EXECUTING,
            tenant_id=T.tenant_id,
            priority="normal",
            dry_run=False,
            created_at="",
        )
        svc._goals[f"r{i}"] = r

    metrics = asyncio.run(svc.get_metrics(tenant_ctx=T))
    # 5 complete out of 10 terminal = 50%, NOT 5/15 = 33%
    assert metrics["success_rate"] == pytest.approx(0.5, abs=0.01)
