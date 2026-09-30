"""A queued ``multi_agent`` goal runs the workflow path on the worker.

Regression: with a task queue configured GoalService hands every goal to the
Celery worker, and ``run_goal`` never looked at ``workflow_mode`` — so a
``multi_agent`` workflow request silently ran as a plain single-agent
AgentGraph goal in production (the in-process path runs the static
WorkflowPlanner/WorkflowExecutor). The worker now runs the same workflow path.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import app.agent.graph as graph_mod
    import app.agent.workflow_executor as wf_mod
    from app.scaling import tasks

    seen: dict[str, Any] = {"graph_runs": 0, "workflow_runs": [], "events": [],
                            "result": {"status": "complete"}}

    class _Graph:
        def __init__(self, **kwargs: Any) -> None:
            self._pause_gate: Any = None

        async def run(self, **kwargs: Any) -> Any:
            seen["graph_runs"] += 1
            raise AssertionError("a multi_agent goal must not run as a single AgentGraph")

    class _Executor:
        def __init__(self, **kwargs: Any) -> None:
            seen["executor_kwargs"] = kwargs

        async def execute(self, plan: Any, tenant_ctx: Any, **kwargs: Any) -> dict[str, Any]:
            seen["workflow_runs"].append((plan, tenant_ctx.tenant_id, kwargs.get("goal")))
            cb = kwargs.get("event_callback")
            if cb is not None:
                await cb({"type": "workflow_step_complete", "step": "research"})
            return dict(seen["result"])

    async def _append(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(graph_mod, "AgentGraph", _Graph)
    monkeypatch.setattr(wf_mod, "WorkflowExecutor", _Executor)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: None)
    monkeypatch.setenv("ENVIRONMENT", "development")
    return seen


def test_queued_multi_agent_goal_executes_the_workflow_path(worker: dict[str, Any]) -> None:
    from app.scaling import tasks

    result = tasks.run_goal.run(
        "g-wf-1", "tenant-wf", "research and summarise vendors", "normal", False,
        workflow_mode="multi_agent",
    )

    assert worker["graph_runs"] == 0
    assert len(worker["workflow_runs"]) == 1
    plan, tenant_id, goal = worker["workflow_runs"][0]
    assert tenant_id == "tenant-wf" and goal == "research and summarise vendors"
    assert plan is not None
    assert worker["executor_kwargs"].get("tool_gate") is not None, "tools stay governed"
    # CORE-15: retrieval steps get the worker's gateway (they had none).
    assert worker["executor_kwargs"].get("retrieval_gateway") is not None
    assert result["status"] == "complete"
    assert result["workflow_mode"] == "multi_agent"


def test_failed_workflow_is_reported_as_failed(worker: dict[str, Any]) -> None:
    from app.scaling import tasks

    worker["result"] = {"status": "failed", "reason": "step 2 failed"}
    result = tasks.run_goal.run(
        "g-wf-2", "tenant-wf", "do it", "normal", False, workflow_mode="multi_agent"
    )
    assert result["status"] == "failed"
    assert worker["graph_runs"] == 0


def test_multi_agent_goal_is_not_downgraded_by_required_isolation(
    worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import dataclasses

    import app.core.runtime_flags as flags_mod
    from app.scaling import tasks

    isolated = dataclasses.replace(
        flags_mod.get_runtime_flags(),
        isolated_agent_execution=True,
        isolated_execution_required=True,
    )
    monkeypatch.setattr(flags_mod, "get_runtime_flags", lambda: isolated)
    result = tasks.run_goal.run(
        "g-wf-iso", "tenant-wf", "do it", "normal", False, workflow_mode="multi_agent"
    )
    assert result["status"] == "failed"
    assert result["reason"] == "workflow_mode_unsupported_in_isolation"
    assert worker["graph_runs"] == 0 and worker["workflow_runs"] == []


def test_single_agent_goal_still_runs_the_agent_graph(
    worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agent.graph as graph_mod
    from app.scaling import tasks

    class _State:
        class Status:
            value = "complete"

        status = Status()
        iterations = 1

    class _Graph:
        def __init__(self, **kwargs: Any) -> None:
            self._pause_gate: Any = None

        async def run(self, **kwargs: Any) -> Any:
            worker["graph_runs"] += 1
            return _State()

    monkeypatch.setattr(graph_mod, "AgentGraph", _Graph)
    result = tasks.run_goal.run("g-wf-3", "tenant-wf", "do it", "normal", False)
    assert result["status"] == "complete"
    assert worker["graph_runs"] == 1 and worker["workflow_runs"] == []
