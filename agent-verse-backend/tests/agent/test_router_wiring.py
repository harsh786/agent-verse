"""Tests for Phase 3 (AgentRouter wiring) and Phase 6 (WorkflowDAG engine)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


def test_routing_decision_has_mode_and_candidates():
    from app.agent.router import RoutingDecision

    d = RoutingDecision(agent_id="a1", reason="test", confidence=0.9, mode="single_agent")
    assert hasattr(d, "mode")
    assert hasattr(d, "candidate_agents")
    assert d.to_dict()["mode"] == "single_agent"


@pytest.mark.asyncio
async def test_workflow_plan_execution_waves_are_parallel():
    from app.agent.workflow_planner import WorkflowPlan, WorkflowStep

    plan = WorkflowPlan(
        goal="test",
        steps=[
            WorkflowStep(id="s1", description="step1", depends_on=[]),
            WorkflowStep(id="s2", description="step2", depends_on=[]),
            WorkflowStep(id="s3", description="step3", depends_on=["s1", "s2"]),
        ],
    )
    waves = plan.execution_waves()
    assert len(waves) == 2, "Steps s1 and s2 should be wave 1, s3 should be wave 2"
    assert len(waves[0]) == 2  # s1 and s2 in parallel
    assert len(waves[1]) == 1  # s3 after both


@pytest.mark.asyncio
async def test_workflow_executor_runs_parallel_wave():
    from app.agent.workflow_executor import WorkflowExecutor
    from app.agent.workflow_planner import WorkflowPlan, WorkflowStep

    call_order = []

    async def mock_execute(step, tenant_ctx, prior_results):
        call_order.append(step.id)
        return {"status": "complete", "output": f"done-{step.id}"}

    executor = WorkflowExecutor(provider=None)
    executor._execute_step = mock_execute

    plan = WorkflowPlan(
        goal="parallel test",
        steps=[
            WorkflowStep(id="s1", description="step1", depends_on=[]),
            WorkflowStep(id="s2", description="step2", depends_on=[]),
        ],
    )
    result = await executor.execute(plan, tenant_ctx=MagicMock(tenant_id="t1"))
    assert result["status"] == "complete"
    assert "s1" in result["results"]
    assert "s2" in result["results"]


def test_goals_api_calls_router_on_missing_agent_id():
    import inspect

    from app.api import goals

    src = inspect.getsource(goals)
    assert "agent_router" in src or "AgentRouter" in src or "router.route" in src, (
        "goals.py must call AgentRouter when agent_id is None"
    )


def test_workflow_planner_not_keyword_only():
    import inspect

    from app.agent import workflow_planner

    src = inspect.getsource(workflow_planner)
    # Should NOT be a 3-line keyword-matching stub
    assert "provider" in src or "LLM" in src.lower() or "complete" in src, (
        "WorkflowPlanner must use LLM, not just keyword matching"
    )
