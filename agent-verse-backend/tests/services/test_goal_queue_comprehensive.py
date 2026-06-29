"""Comprehensive tests for app/services/goal_queue.py — targeting 90%+ coverage."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ── GoalTaskQueue protocol ────────────────────────────────────────────────────

class TestGoalTaskQueueProtocol:
    def test_protocol_has_enqueue_goal(self) -> None:
        from app.services.goal_queue import GoalTaskQueue
        import inspect
        # Protocol methods should be visible
        assert hasattr(GoalTaskQueue, "enqueue_goal")

    def test_protocol_satisfied_by_concrete_impl(self) -> None:
        from app.services.goal_queue import CeleryGoalTaskQueue, GoalTaskQueue
        import typing
        # CeleryGoalTaskQueue should satisfy the protocol
        obj = CeleryGoalTaskQueue()
        assert hasattr(obj, "enqueue_goal")
        assert callable(obj.enqueue_goal)


# ── CeleryGoalTaskQueue ───────────────────────────────────────────────────────

class TestCeleryGoalTaskQueue:
    def test_enqueue_goal_free_plan(self) -> None:
        from app.services.goal_queue import CeleryGoalTaskQueue

        mock_result = MagicMock()
        mock_result.id = "celery-task-id-123"

        mock_task = MagicMock()
        mock_task.apply_async = MagicMock(return_value=mock_result)

        with patch("app.scaling.tasks.run_goal", mock_task):
            with patch("app.scaling.celery_app.PLAN_QUEUE_MAP", {"free": "goals.free"}):
                queue = CeleryGoalTaskQueue()
                task_id = queue.enqueue_goal(
                    goal_id="g1",
                    tenant_id="t1",
                    goal_text="Deploy service",
                    priority="normal",
                    dry_run=False,
                    plan="free",
                )

        assert task_id == "celery-task-id-123"
        mock_task.apply_async.assert_called_once()
        call_kwargs = mock_task.apply_async.call_args[1]
        assert call_kwargs["queue"] == "goals.free"

    def test_enqueue_goal_enterprise_plan(self) -> None:
        from app.services.goal_queue import CeleryGoalTaskQueue

        mock_result = MagicMock()
        mock_result.id = "enterprise-task-id"

        mock_task = MagicMock()
        mock_task.apply_async = MagicMock(return_value=mock_result)

        with patch("app.scaling.tasks.run_goal", mock_task):
            with patch(
                "app.scaling.celery_app.PLAN_QUEUE_MAP",
                {"enterprise": "goals.enterprise"},
            ):
                queue = CeleryGoalTaskQueue()
                task_id = queue.enqueue_goal(
                    goal_id="g2",
                    tenant_id="t1",
                    goal_text="Scale infrastructure",
                    priority="high",
                    dry_run=False,
                    plan="enterprise",
                )

        assert task_id == "enterprise-task-id"
        call_kwargs = mock_task.apply_async.call_args[1]
        assert call_kwargs["queue"] == "goals.enterprise"

    def test_enqueue_goal_unknown_plan_defaults_to_free(self) -> None:
        from app.services.goal_queue import CeleryGoalTaskQueue

        mock_result = MagicMock()
        mock_result.id = "task-id"

        mock_task = MagicMock()
        mock_task.apply_async = MagicMock(return_value=mock_result)

        with patch("app.scaling.tasks.run_goal", mock_task):
            with patch("app.scaling.celery_app.PLAN_QUEUE_MAP", {}):
                queue = CeleryGoalTaskQueue()
                task_id = queue.enqueue_goal(
                    goal_id="g3",
                    tenant_id="t1",
                    goal_text="test",
                    priority="normal",
                    dry_run=True,
                    plan="platinum",  # unknown plan
                )

        call_kwargs = mock_task.apply_async.call_args[1]
        assert call_kwargs["queue"] == "goals.free"

    def test_enqueue_goal_with_agent_id(self) -> None:
        from app.services.goal_queue import CeleryGoalTaskQueue

        mock_result = MagicMock()
        mock_result.id = "task-with-agent"

        mock_task = MagicMock()
        mock_task.apply_async = MagicMock(return_value=mock_result)

        with patch("app.scaling.tasks.run_goal", mock_task):
            with patch("app.scaling.celery_app.PLAN_QUEUE_MAP", {"free": "goals.free"}):
                queue = CeleryGoalTaskQueue()
                task_id = queue.enqueue_goal(
                    goal_id="g4",
                    tenant_id="t1",
                    goal_text="test",
                    priority="normal",
                    dry_run=False,
                    agent_id="agent-abc",
                    plan="free",
                )

        kwargs = mock_task.apply_async.call_args[1]["kwargs"]
        assert kwargs["agent_id"] == "agent-abc"

    def test_enqueue_goal_with_workflow_mode(self) -> None:
        from app.services.goal_queue import CeleryGoalTaskQueue

        mock_result = MagicMock()
        mock_result.id = "task-wf"

        mock_task = MagicMock()
        mock_task.apply_async = MagicMock(return_value=mock_result)

        with patch("app.scaling.tasks.run_goal", mock_task):
            with patch("app.scaling.celery_app.PLAN_QUEUE_MAP", {"free": "goals.free"}):
                queue = CeleryGoalTaskQueue()
                queue.enqueue_goal(
                    goal_id="g5",
                    tenant_id="t1",
                    goal_text="orchestrate",
                    priority="normal",
                    dry_run=False,
                    workflow_mode="supervisor",
                    plan="free",
                )

        kwargs = mock_task.apply_async.call_args[1]["kwargs"]
        assert kwargs["workflow_mode"] == "supervisor"

    def test_enqueue_goal_none_agent_id_becomes_empty_string(self) -> None:
        from app.services.goal_queue import CeleryGoalTaskQueue

        mock_result = MagicMock()
        mock_result.id = "task-id"

        mock_task = MagicMock()
        mock_task.apply_async = MagicMock(return_value=mock_result)

        with patch("app.scaling.tasks.run_goal", mock_task):
            with patch("app.scaling.celery_app.PLAN_QUEUE_MAP", {"free": "goals.free"}):
                queue = CeleryGoalTaskQueue()
                queue.enqueue_goal(
                    goal_id="g6",
                    tenant_id="t1",
                    goal_text="test",
                    priority="normal",
                    dry_run=False,
                    agent_id=None,
                    plan="free",
                )

        kwargs = mock_task.apply_async.call_args[1]["kwargs"]
        assert kwargs["agent_id"] == ""  # None → ""

    def test_enqueue_goal_returns_string_task_id(self) -> None:
        from app.services.goal_queue import CeleryGoalTaskQueue

        mock_result = MagicMock()
        mock_result.id = 12345  # Non-string id

        mock_task = MagicMock()
        mock_task.apply_async = MagicMock(return_value=mock_result)

        with patch("app.scaling.tasks.run_goal", mock_task):
            with patch("app.scaling.celery_app.PLAN_QUEUE_MAP", {"free": "goals.free"}):
                queue = CeleryGoalTaskQueue()
                task_id = queue.enqueue_goal(
                    goal_id="g7",
                    tenant_id="t1",
                    goal_text="test",
                    priority="normal",
                    dry_run=False,
                    plan="free",
                )

        assert isinstance(task_id, str)
        assert task_id == "12345"

    def test_enqueue_goal_result_without_id_attr(self) -> None:
        from app.services.goal_queue import CeleryGoalTaskQueue

        mock_result = object()  # no .id attribute

        mock_task = MagicMock()
        mock_task.apply_async = MagicMock(return_value=mock_result)

        with patch("app.scaling.tasks.run_goal", mock_task):
            with patch("app.scaling.celery_app.PLAN_QUEUE_MAP", {"free": "goals.free"}):
                queue = CeleryGoalTaskQueue()
                task_id = queue.enqueue_goal(
                    goal_id="g8",
                    tenant_id="t1",
                    goal_text="test",
                    priority="normal",
                    dry_run=False,
                    plan="free",
                )

        assert task_id == ""  # getattr fallback

    def test_enqueue_goal_with_goal_template(self) -> None:
        from app.services.goal_queue import CeleryGoalTaskQueue

        mock_result = MagicMock()
        mock_result.id = "task-tmpl"

        mock_task = MagicMock()
        mock_task.apply_async = MagicMock(return_value=mock_result)

        with patch("app.scaling.tasks.run_goal", mock_task):
            with patch("app.scaling.celery_app.PLAN_QUEUE_MAP", {"free": "goals.free"}):
                queue = CeleryGoalTaskQueue()
                queue.enqueue_goal(
                    goal_id="g9",
                    tenant_id="t1",
                    goal_text="test",
                    priority="normal",
                    dry_run=False,
                    goal_template="standard_deploy_v1",
                    plan="free",
                )

        kwargs = mock_task.apply_async.call_args[1]["kwargs"]
        assert kwargs["goal_template"] == "standard_deploy_v1"
