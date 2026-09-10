"""Tests for WorkflowTestRunner — dry-run, step-through, scenarios."""
from __future__ import annotations

import pytest

from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.test_runner import (
    MockToolAdapter,
    WorkflowScenario,
    WorkflowTestRunner,
)


@pytest.fixture
def runner() -> WorkflowTestRunner:
    return WorkflowTestRunner()


def _linear_wf() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="Linear",
        steps=[
            StepDefinition(id="step_a", type="tool", tool="t"),
            StepDefinition(id="step_b", type="llm", prompt="do something", depends_on=["step_a"]),
            StepDefinition(id="step_c", type="http", url="https://example.com", depends_on=["step_b"]),
        ],
    )


# ── Dry run ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dry_run_completes(runner: WorkflowTestRunner) -> None:
    wf = _linear_wf()
    result = await runner.dry_run(wf, inputs={})
    assert result.status == "complete"
    assert result.passed is True
    assert result.error is None


@pytest.mark.asyncio
async def test_dry_run_all_steps_have_outputs(runner: WorkflowTestRunner) -> None:
    wf = _linear_wf()
    result = await runner.dry_run(wf, inputs={})
    for step in wf.steps:
        assert step.id in result.step_outputs


@pytest.mark.asyncio
async def test_dry_run_mock_overrides(runner: WorkflowTestRunner) -> None:
    wf = _linear_wf()
    overrides = {"step_a": {"my_result": "custom value"}}
    result = await runner.dry_run(wf, inputs={}, mock_overrides=overrides)
    assert result.step_outputs["step_a"]["my_result"] == "custom value"


@pytest.mark.asyncio
async def test_dry_run_tool_step_default_mock(runner: WorkflowTestRunner) -> None:
    wf = _linear_wf()
    result = await runner.dry_run(wf, inputs={})
    assert result.step_outputs["step_a"]["success"] is True


@pytest.mark.asyncio
async def test_dry_run_llm_step_default_mock(runner: WorkflowTestRunner) -> None:
    wf = _linear_wf()
    result = await runner.dry_run(wf, inputs={})
    assert "result" in result.step_outputs["step_b"]


@pytest.mark.asyncio
async def test_dry_run_http_step_default_mock(runner: WorkflowTestRunner) -> None:
    wf = _linear_wf()
    result = await runner.dry_run(wf, inputs={})
    assert result.step_outputs["step_c"]["status_code"] == 200


# ── Scenario runner ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_pass(runner: WorkflowTestRunner) -> None:
    wf = _linear_wf()
    scenario = WorkflowScenario(
        name="happy path",
        inputs={},
        expected_status="complete",
    )
    result = await runner.run_scenario(wf, scenario)
    assert result.passed is True
    assert result.assertion_failures == []


@pytest.mark.asyncio
async def test_scenario_fail_on_wrong_status(runner: WorkflowTestRunner) -> None:
    wf = _linear_wf()
    scenario = WorkflowScenario(
        name="expects failure",
        inputs={},
        expected_status="failed",  # won't match; dry run always completes
    )
    result = await runner.run_scenario(wf, scenario)
    assert result.passed is False
    assert any("status" in f for f in result.assertion_failures)


@pytest.mark.asyncio
async def test_scenario_step_assertion_pass(runner: WorkflowTestRunner) -> None:
    wf = _linear_wf()
    scenario = WorkflowScenario(
        name="assert step output",
        inputs={},
        mock_overrides={"step_a": {"result": "hello"}},
        step_assertions={"step_a": {"result": "hello"}},
    )
    result = await runner.run_scenario(wf, scenario)
    assert result.passed is True


@pytest.mark.asyncio
async def test_scenario_step_assertion_fail(runner: WorkflowTestRunner) -> None:
    wf = _linear_wf()
    scenario = WorkflowScenario(
        name="wrong assertion",
        inputs={},
        mock_overrides={"step_a": {"result": "hello"}},
        step_assertions={"step_a": {"result": "wrong value"}},
    )
    result = await runner.run_scenario(wf, scenario)
    assert result.passed is False


@pytest.mark.asyncio
async def test_run_multiple_scenarios(runner: WorkflowTestRunner) -> None:
    wf = _linear_wf()
    scenarios = [
        WorkflowScenario(name="s1", inputs={}, expected_status="complete"),
        WorkflowScenario(name="s2", inputs={}, expected_status="complete"),
    ]
    results = await runner.run_scenarios(wf, scenarios)
    assert len(results) == 2
    assert all(r.passed for r in results)


# ── Step-through ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step_through_returns_all_steps(runner: WorkflowTestRunner) -> None:
    wf = _linear_wf()
    steps_log = await runner.step_through(wf, inputs={})
    assert len(steps_log) == 3
    assert steps_log[0]["step_id"] == "step_a"
    assert steps_log[-1]["step_id"] == "step_c"


@pytest.mark.asyncio
async def test_step_through_with_override(runner: WorkflowTestRunner) -> None:
    wf = _linear_wf()
    steps_log = await runner.step_through(wf, inputs={}, step_overrides={"step_b": {"custom": True}})
    step_b = next(s for s in steps_log if s["step_id"] == "step_b")
    assert step_b["output"]["custom"] is True


# ── MockToolAdapter ───────────────────────────────────────────────────────────


def test_mock_adapter_default_tool_output() -> None:
    adapter = MockToolAdapter()
    out = adapter.mock_output("my_step", "tool")
    assert out["success"] is True


def test_mock_adapter_default_hitl_output() -> None:
    adapter = MockToolAdapter()
    out = adapter.mock_output("review", "hitl")
    assert out["action"] == "approved"


def test_mock_adapter_override_takes_priority() -> None:
    adapter = MockToolAdapter(overrides={"my_step": {"custom_key": "custom_val"}})
    out = adapter.mock_output("my_step", "tool")
    assert out["custom_key"] == "custom_val"


def test_mock_adapter_unknown_type_fallback() -> None:
    adapter = MockToolAdapter()
    out = adapter.mock_output("s", "unknown_type")
    assert "_mock" in out


# ── Topo sort ─────────────────────────────────────────────────────────────────


def test_topo_sort_linear() -> None:
    wf = _linear_wf()
    ordered = WorkflowTestRunner._topo_sort(wf)
    ids = [s.id for s in ordered]
    assert ids.index("step_a") < ids.index("step_b") < ids.index("step_c")
