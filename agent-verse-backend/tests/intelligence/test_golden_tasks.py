"""Tests for P2.6 golden tasks and rollout gate."""
import asyncio

import pytest

from tests._paths import MIGRATIONS_DIR


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

    # No DB → the eval-suite store's in-memory mode; the suite has never run.
    result = await check_agent_rollout_gate(
        agent_id="a1", eval_suite_id="s1", tenant_id="t-golden-gate", db=None
    )
    assert result["gate_passed"] is False
    assert result["run_count"] == 0


def test_golden_task_crud_lives_on_the_versioned_store():
    # MEM-54: the unversioned add_golden_task/get_golden_tasks helpers are gone;
    # golden tasks are revision rows managed by EvalSuiteStore.
    import app.intelligence.eval_suite as eval_suite
    from app.intelligence.eval_suite_store import EvalSuiteStore

    assert not hasattr(eval_suite, "add_golden_task")
    for name in ("add_task", "update_task", "delete_task", "import_tasks", "export"):
        assert asyncio.iscoroutinefunction(getattr(EvalSuiteStore, name))


def test_migration_0038_exists():
    import os

    files = os.listdir(MIGRATIONS_DIR)
    assert any("0038" in f for f in files)


def test_migration_0039_exists():
    import os

    files = os.listdir(MIGRATIONS_DIR)
    assert any("0039" in f for f in files)
