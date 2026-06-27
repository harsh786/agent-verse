"""Tests for GoalRecord eviction correctness."""
import pytest
from datetime import UTC, datetime, timedelta
from app.services.goal_service import GoalRecord, GoalService
from app.agent.state import GoalStatus
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="ev-t1", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _completed_record(goal_id: str, hours_ago: float) -> GoalRecord:
    record = GoalRecord(
        goal_id=goal_id,
        goal_text="test",
        status=GoalStatus.COMPLETE,
        tenant_id=T.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="",
    )
    dt = datetime.now(UTC) - timedelta(hours=hours_ago)
    record.completed_at = dt.isoformat()
    return record


def test_eviction_keeps_recent_completed():
    """Goals completed less than 1h ago should NOT be evicted."""
    svc = GoalService()
    svc._goals["new"] = _completed_record("new", hours_ago=0.1)
    svc._goals["old"] = _completed_record("old", hours_ago=2.0)
    evicted = svc._evict_stale_goals()
    assert evicted == 1
    assert "new" in svc._goals
    assert "old" not in svc._goals


def test_eviction_keeps_running_goals():
    """In-progress goals must never be evicted."""
    svc = GoalService()
    r = GoalRecord(
        goal_id="run1",
        goal_text="t",
        status=GoalStatus.EXECUTING,
        tenant_id=T.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="",
    )
    svc._goals["run1"] = r
    svc._evict_stale_goals()
    assert "run1" in svc._goals


def test_eviction_skips_goals_without_completed_at():
    """Goals without completed_at should not be evicted (timestamp missing = recent)."""
    svc = GoalService()
    r = GoalRecord(
        goal_id="notimestamp",
        goal_text="t",
        status=GoalStatus.COMPLETE,
        tenant_id=T.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="",
    )
    svc._goals["notimestamp"] = r
    svc._evict_stale_goals()
    assert "notimestamp" in svc._goals, "Goal without timestamp should not be evicted"
