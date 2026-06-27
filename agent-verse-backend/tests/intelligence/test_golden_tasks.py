"""Tests for P2.6 golden tasks and rollout gate."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock


def test_golden_task_class_exists():
    from app.intelligence.eval_suite import GoldenTask

    task = GoldenTask(
        goal="Find all open issues",
        expected_output_contains="issues",
        min_score=0.9,
    )
    assert task.goal == "Find all open issues"
    assert task.min_score == 0.9
    assert task.task_id  # auto-generated


def test_golden_task_backward_compat():
    """Existing code using expected_tools and list-style expected_output_contains still works."""
    from app.intelligence.eval_suite import GoldenTask

    task = GoldenTask(
        goal="Test goal",
        expected_tools=["jira.search"],
        forbidden_tools=["jira.delete"],
        expected_output_contains=["result"],
        suite_id="s1",
        max_iterations=10,
    )
    assert task.expected_tools == ["jira.search"]
    assert task.expected_tool_calls == ["jira.search"]
    assert task.forbidden_tools == ["jira.delete"]
    assert task.suite_id == "s1"
    assert task.max_iterations == 10
    # expected_output_contains stored as list
    assert isinstance(task.expected_output_contains, list)


def test_check_rollout_gate_exists():
    from app.intelligence.eval_suite import check_agent_rollout_gate

    assert asyncio.iscoroutinefunction(check_agent_rollout_gate)


@pytest.mark.asyncio
async def test_rollout_gate_fails_without_data():
    from app.intelligence.eval_suite import check_agent_rollout_gate

    # Build a proper async context manager mock
    mock_execute_result = MagicMock()
    mock_execute_result.fetchone = lambda: (None, 0, 0)

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    # db() must return the context manager directly (not as a coroutine)
    mock_db = MagicMock(return_value=mock_session)

    result = await check_agent_rollout_gate(
        agent_id="a1", eval_suite_id="s1", tenant_id="t1", db=mock_db
    )
    assert result["gate_passed"] is False
    assert result["run_count"] == 0


def test_add_golden_task_is_coroutine():
    from app.intelligence.eval_suite import add_golden_task

    assert asyncio.iscoroutinefunction(add_golden_task)


def test_get_golden_tasks_is_coroutine():
    from app.intelligence.eval_suite import get_golden_tasks

    assert asyncio.iscoroutinefunction(get_golden_tasks)


def test_migration_0038_exists():
    import os

    files = os.listdir(
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/"
        "agent-verse-backend/app/db/migrations/versions"
    )
    assert any("0038" in f for f in files)


def test_migration_0039_exists():
    import os

    files = os.listdir(
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/"
        "agent-verse-backend/app/db/migrations/versions"
    )
    assert any("0039" in f for f in files)
