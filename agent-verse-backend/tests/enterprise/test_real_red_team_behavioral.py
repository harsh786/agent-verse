"""Tests for BehavioralRedTeamRunner."""
from __future__ import annotations

import pytest

from app.enterprise.red_team import BehavioralRedTeamRunner, RedTeamReport
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="rt-behav-test", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


# ─── Helpers ──────────────────────────────────────────────────────────────────

class _GoalSvc:
    """Minimal goal_service that drives a scripted event sequence per goal."""

    def __init__(self, events: list[dict]) -> None:
        self._events = events

    async def submit_goal(self, *, goal, priority, dry_run, tenant_ctx):
        return {"goal_id": "mock-goal-id"}

    async def subscribe_events(self, *, goal_id, tenant_ctx):
        for evt in self._events:
            yield evt


# ─── Tests ────────────────────────────────────────────────────────────────────


def test_behavioral_red_team_runner_instantiates():
    """BehavioralRedTeamRunner can be instantiated without arguments."""
    runner = BehavioralRedTeamRunner()
    assert runner is not None


def test_analyze_events_returns_true_when_tool_call_denied():
    """_analyze_events returns True when a tool_call_denied event is present."""
    runner = BehavioralRedTeamRunner()
    events = [{"type": "tool_call_denied", "tool": "dangerous_tool"}]
    case = {"id": "test", "description": "test case"}

    assert runner._analyze_events(events, case) is True


def test_analyze_events_returns_true_when_goal_failed_with_guardrail():
    """_analyze_events returns True when goal_failed contains a guardrail keyword."""
    runner = BehavioralRedTeamRunner()
    events = [{"type": "goal_failed", "reason": "guardrail blocked injection attempt"}]
    case = {"id": "test", "description": "test case"}

    assert runner._analyze_events(events, case) is True


def test_analyze_events_returns_false_when_goal_complete_normally():
    """_analyze_events returns False when goal completed normally (no blocking)."""
    runner = BehavioralRedTeamRunner()
    events = [
        {"type": "tool_call_complete", "tool_name": "safe_tool", "output": "ok"},
        {"type": "goal_complete"},
    ]
    case = {"id": "normal", "description": "normal completion"}

    assert runner._analyze_events(events, case) is False


@pytest.mark.asyncio
async def test_run_behavioral_requires_goal_service():
    """run_behavioral raises TypeError when goal_service is not supplied."""
    runner = BehavioralRedTeamRunner()
    with pytest.raises(TypeError):
        await runner.run_behavioral(tenant_ctx=_CTX)  # missing required goal_service


@pytest.mark.asyncio
async def test_red_team_report_correct_structure_after_behavioral_run():
    """RedTeamReport has correct structure after a behavioral run."""
    runner = BehavioralRedTeamRunner()
    # All adversarial cases → error event (simulating guardrail at submission)
    svc = _GoalSvc([{"type": "error", "reason": "guardrail blocked"}])

    report = await runner.run_behavioral(
        goal_service=svc,
        tenant_ctx=_CTX,
        cases=["prompt_injection"],  # run just one case for speed
    )

    assert isinstance(report, RedTeamReport)
    assert report.cases_run == 1
    assert report.cases_passed + report.cases_failed == report.cases_run
    assert len(report.results) == 1
    result = report.results[0]
    assert "case_id" in result
    assert "name" in result
    assert "status" in result
    assert "detected" in result
    assert "outcome" in result
