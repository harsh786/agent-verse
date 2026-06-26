"""Tests for EvalSuiteRunner."""
from __future__ import annotations

import pytest

from app.intelligence.eval_suite import (
    EvalSuiteResult,
    EvalSuiteRunner,
    GoldenTask,
    GoldenTaskResult,
)
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="eval-suite-test", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


# ─── Helpers ──────────────────────────────────────────────────────────────────

class _MockGoalService:
    """Minimal goal_service stub that drives a scripted sequence of events."""

    def __init__(self, events: list[dict]) -> None:
        self._events = events

    async def submit_goal(self, *, goal, priority, dry_run, tenant_ctx):
        return {"goal_id": "mock-goal-id"}

    async def subscribe_events(self, *, goal_id, tenant_ctx):
        for evt in self._events:
            yield evt


def _svc(*events):
    """Build a MockGoalService with the given event dicts."""
    return _MockGoalService(list(events))


# ─── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_suite_no_tasks_returns_empty_result():
    """run_suite with no tasks returns EvalSuiteResult with total=0."""
    runner = EvalSuiteRunner()
    runner.create_suite("empty-suite")
    svc = _svc({"type": "goal_complete"})

    result = await runner.run_suite("empty-suite", svc, _CTX)

    assert isinstance(result, EvalSuiteResult)
    assert result.total_tasks == 0
    assert result.passed_tasks == 0
    assert result.failed_tasks == 0
    assert result.task_results == []


@pytest.mark.asyncio
async def test_run_suite_passes_task_when_all_conditions_met():
    """run_suite passes task when all conditions met (tool called + output present)."""
    runner = EvalSuiteRunner()
    task = GoldenTask(
        goal="do something",
        expected_tools=["my_tool"],
        expected_output_contains=["hello"],
    )
    runner.create_suite("suite1", [task])
    svc = _svc(
        {"type": "tool_call_complete", "tool_name": "my_tool", "output": "hello world"},
        {"type": "goal_complete"},
    )

    result = await runner.run_suite("suite1", svc, _CTX)

    assert result.total_tasks == 1
    assert result.passed_tasks == 1
    assert result.failed_tasks == 0
    assert result.task_results[0].passed is True
    assert result.task_results[0].failure_reasons == []


@pytest.mark.asyncio
async def test_run_suite_fails_task_when_required_tool_not_called():
    """run_suite fails task when a required tool was not called."""
    runner = EvalSuiteRunner()
    task = GoldenTask(goal="do something", expected_tools=["missing_tool"])
    runner.create_suite("suite2", [task])
    svc = _svc({"type": "goal_complete"})

    result = await runner.run_suite("suite2", svc, _CTX)

    assert result.passed_tasks == 0
    assert result.failed_tasks == 1
    task_res = result.task_results[0]
    assert task_res.passed is False
    assert any("missing_tool" in r for r in task_res.failure_reasons)


@pytest.mark.asyncio
async def test_run_suite_fails_task_when_forbidden_tool_was_called():
    """run_suite fails task when a forbidden tool was called."""
    runner = EvalSuiteRunner()
    task = GoldenTask(goal="do something", forbidden_tools=["bad_tool"])
    runner.create_suite("suite3", [task])
    svc = _svc(
        {"type": "tool_call_complete", "tool_name": "bad_tool", "output": "oops"},
        {"type": "goal_complete"},
    )

    result = await runner.run_suite("suite3", svc, _CTX)

    assert result.passed_tasks == 0
    assert result.failed_tasks == 1
    task_res = result.task_results[0]
    assert task_res.passed is False
    assert any("bad_tool" in r for r in task_res.failure_reasons)


@pytest.mark.asyncio
async def test_run_suite_fails_task_when_output_missing_expected_phrase():
    """run_suite fails task when the expected phrase is absent from output."""
    runner = EvalSuiteRunner()
    task = GoldenTask(
        goal="do something", expected_output_contains=["very specific phrase"]
    )
    runner.create_suite("suite4", [task])
    svc = _svc(
        {"type": "tool_call_complete", "tool_name": "t", "output": "generic text"},
        {"type": "goal_complete"},
    )

    result = await runner.run_suite("suite4", svc, _CTX)

    assert result.failed_tasks == 1
    task_res = result.task_results[0]
    assert any("very specific phrase" in r for r in task_res.failure_reasons)


def test_add_task_adds_to_suite():
    """add_task correctly appends a task to an existing suite."""
    runner = EvalSuiteRunner()
    runner.create_suite("s")
    t1 = GoldenTask(goal="first")
    t2 = GoldenTask(goal="second")

    runner.add_task("s", t1)
    runner.add_task("s", t2)

    assert len(runner._suites["s"]) == 2
    assert runner._suites["s"][0].goal == "first"
    assert runner._suites["s"][1].goal == "second"


def test_list_suites_returns_suite_ids():
    """list_suites returns the IDs of all created suites."""
    runner = EvalSuiteRunner()
    runner.create_suite("alpha")
    runner.create_suite("beta")

    suites = runner.list_suites()

    assert "alpha" in suites
    assert "beta" in suites


@pytest.mark.asyncio
async def test_pass_rate_calculation_correct():
    """pass_rate equals passed_tasks / total_tasks."""
    runner = EvalSuiteRunner()
    # 2 tasks: one with a required tool (will pass), one with a missing tool (will fail)
    t_pass = GoldenTask(goal="ok")
    t_fail = GoldenTask(goal="fail", expected_tools=["not_called"])
    runner.create_suite("rates", [t_pass, t_fail])
    svc = _svc({"type": "goal_complete"})

    result = await runner.run_suite("rates", svc, _CTX)

    assert result.total_tasks == 2
    assert result.passed_tasks == 1
    assert result.failed_tasks == 1
    assert abs(result.pass_rate - 0.5) < 1e-9
