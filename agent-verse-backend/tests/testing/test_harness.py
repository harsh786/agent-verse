"""Tests for AgentTestHarness."""
import pytest
from app.testing.harness import AgentTestHarness, TestResult


def test_harness_instantiates():
    harness = AgentTestHarness()
    assert harness is not None


def test_set_mock_tool_chaining():
    harness = AgentTestHarness()
    result = harness.set_mock_tool("jira.list_issues", []).set_mock_tool("github.list_prs", [])
    assert result is harness


@pytest.mark.asyncio
async def test_run_goal_returns_test_result():
    harness = AgentTestHarness()
    result = await harness.run_goal("Complete the test goal")
    assert isinstance(result, TestResult)
    assert result.events is not None


@pytest.mark.asyncio
async def test_harness_assert_goal_completed():
    harness = AgentTestHarness(
        verifier_responses=['{"success": true, "reason": "done"}']
    )
    result = await harness.run_goal("Do something")
    harness.assert_goal_completed(result)


def test_assert_tool_called_raises_when_missing():
    result = TestResult(success=True, tools_called=["jira.search"])
    harness = AgentTestHarness()
    with pytest.raises(AssertionError):
        harness.assert_tool_called("github.list_prs", result)


def test_assert_tool_not_called_passes():
    result = TestResult(success=True, tools_called=["jira.search"])
    harness = AgentTestHarness()
    harness.assert_tool_not_called("github.list_prs", result)
