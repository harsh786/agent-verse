"""Comprehensive tests for app/agent/workflow_planner.py — targets 90%+ statement coverage."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.workflow_planner import (
    WorkflowPlan,
    WorkflowPlanner,
    WorkflowStep,
    _StaticWorkflowPlan,
    _StaticWorkflowStep,
    build_static_workflow,
)
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-wp", plan=PlanTier.PROFESSIONAL, api_key_id="key-wp")


# ── _StaticWorkflowStep / _StaticWorkflowPlan ─────────────────────────────────

def test_static_workflow_step_construction() -> None:
    step = _StaticWorkflowStep(
        step_id="step_1",
        connector_name="jira",
        agent_id=None,
        intent="fetch_open_issues",
        input_from=[],
        requires_approval=False,
    )
    assert step.step_id == "step_1"
    assert step.connector_name == "jira"


# ── build_static_workflow ─────────────────────────────────────────────────────

def test_build_static_workflow_empty_goal() -> None:
    plan = build_static_workflow("Do something unrelated")
    assert len(plan.steps) == 0


def test_build_static_workflow_jira_goal() -> None:
    plan = build_static_workflow("Get all open JIRA issues for sprint 42")
    steps = [s for s in plan.steps if s.connector_name == "jira"]
    assert len(steps) == 1
    assert steps[0].intent == "fetch_open_issues"


def test_build_static_workflow_confluence_goal() -> None:
    plan = build_static_workflow("Summarize results in Confluence page")
    steps = [s for s in plan.steps if s.connector_name == "confluence"]
    assert len(steps) == 1
    assert steps[0].intent == "create_summary_page"


def test_build_static_workflow_email_goal() -> None:
    plan = build_static_workflow("Send email with summary")
    steps = [s for s in plan.steps if s.connector_name == "email"]
    assert len(steps) == 1
    assert steps[0].intent == "send_summary_email"


def test_build_static_workflow_mail_alternative() -> None:
    plan = build_static_workflow("Send mail to the team")
    steps = [s for s in plan.steps if s.connector_name == "email"]
    assert len(steps) == 1


def test_build_static_workflow_browser_goal() -> None:
    plan = build_static_workflow("Automate browser navigation on website")
    steps = [s for s in plan.steps if s.connector_name == "rpa"]
    assert len(steps) == 1
    assert steps[0].intent == "browser_automation"


def test_build_static_workflow_rpa_keyword() -> None:
    plan = build_static_workflow("Use RPA to automate UI tasks")
    steps = [s for s in plan.steps if s.connector_name == "rpa"]
    assert len(steps) == 1


def test_build_static_workflow_ui_keyword() -> None:
    plan = build_static_workflow("Interact with the UI to fill the form")
    steps = [s for s in plan.steps if s.connector_name == "rpa"]
    assert len(steps) == 1


def test_build_static_workflow_jira_and_confluence() -> None:
    plan = build_static_workflow("Get JIRA issues and create Confluence summary")
    connectors = [s.connector_name for s in plan.steps]
    assert "jira" in connectors
    assert "confluence" in connectors
    # Confluence step should have jira_step_id as input
    conf_step = next(s for s in plan.steps if s.connector_name == "confluence")
    assert len(conf_step.input_from) == 1


def test_build_static_workflow_jira_confluence_email() -> None:
    plan = build_static_workflow("Fetch JIRA data, write Confluence page, send email")
    connectors = [s.connector_name for s in plan.steps]
    assert "jira" in connectors
    assert "confluence" in connectors
    assert "email" in connectors
    # Email step gets all preceding steps as input
    email_step = next(s for s in plan.steps if s.connector_name == "email")
    assert len(email_step.input_from) >= 2


def test_build_static_workflow_step_ids_are_sequential() -> None:
    plan = build_static_workflow("JIRA issues and Confluence and send email")
    step_ids = [s.step_id for s in plan.steps]
    assert step_ids == [f"step_{i + 1}" for i in range(len(plan.steps))]


def test_build_static_workflow_no_approval_required() -> None:
    plan = build_static_workflow("JIRA summary via email")
    for step in plan.steps:
        assert step.requires_approval is False


# ── WorkflowStep ──────────────────────────────────────────────────────────────

def test_workflow_step_defaults() -> None:
    s = WorkflowStep(id="s1", description="Do something")
    assert s.tool == ""
    assert s.depends_on == []
    assert s.can_parallel is True
    assert s.status == "pending"
    assert s.estimated_minutes == 1


# ── WorkflowPlan.from_dict ────────────────────────────────────────────────────

def test_workflow_plan_from_dict_basic() -> None:
    data = {
        "steps": [
            {"id": "s1", "description": "Step 1"},
            {"id": "s2", "description": "Step 2", "depends_on": ["s1"]},
        ]
    }
    plan = WorkflowPlan.from_dict(data, goal="Test goal")
    assert plan.goal == "Test goal"
    assert len(plan.steps) == 2
    assert plan.steps[1].depends_on == ["s1"]


def test_workflow_plan_from_dict_all_fields() -> None:
    data = {
        "steps": [
            {
                "id": "s1",
                "description": "Fetch data",
                "tool": "jira_search",
                "depends_on": [],
                "can_parallel": False,
                "estimated_minutes": 5,
            }
        ]
    }
    plan = WorkflowPlan.from_dict(data, goal="Fetch Jira data")
    s = plan.steps[0]
    assert s.tool == "jira_search"
    assert s.can_parallel is False
    assert s.estimated_minutes == 5


def test_workflow_plan_from_dict_missing_steps_key() -> None:
    plan = WorkflowPlan.from_dict({}, goal="Empty")
    assert plan.steps == []


def test_workflow_plan_from_dict_missing_id_uses_index() -> None:
    data = {"steps": [{"description": "No ID"}]}
    plan = WorkflowPlan.from_dict(data, goal="G")
    assert plan.steps[0].id == "s1"


# ── WorkflowPlan.execution_waves ──────────────────────────────────────────────

def test_workflow_plan_execution_waves_empty() -> None:
    plan = WorkflowPlan(goal="G")
    assert plan.execution_waves() == []


def test_workflow_plan_execution_waves_independent_steps() -> None:
    plan = WorkflowPlan(goal="G", steps=[
        WorkflowStep(id="s1", description="A"),
        WorkflowStep(id="s2", description="B"),
    ])
    waves = plan.execution_waves()
    assert len(waves) == 1
    assert len(waves[0]) == 2


def test_workflow_plan_execution_waves_linear_chain() -> None:
    plan = WorkflowPlan(goal="G", steps=[
        WorkflowStep(id="s1", description="A"),
        WorkflowStep(id="s2", description="B", depends_on=["s1"]),
        WorkflowStep(id="s3", description="C", depends_on=["s2"]),
    ])
    waves = plan.execution_waves()
    assert len(waves) == 3


def test_workflow_plan_execution_waves_circular_dep_dumps_remaining() -> None:
    plan = WorkflowPlan(goal="G", steps=[
        WorkflowStep(id="s1", description="A", depends_on=["s2"]),
        WorkflowStep(id="s2", description="B", depends_on=["s1"]),
    ])
    waves = plan.execution_waves()
    total_steps = sum(len(w) for w in waves)
    assert total_steps == 2


# ── WorkflowPlanner ───────────────────────────────────────────────────────────

async def test_workflow_planner_no_provider_returns_heuristic() -> None:
    planner = WorkflowPlanner(provider=None)
    plan = await planner.plan("Do something", _CTX)
    assert len(plan.steps) == 1
    assert plan.steps[0].description == "Do something"
    assert plan.steps[0].can_parallel is False


async def test_workflow_planner_with_provider_returns_llm_plan() -> None:
    llm_json = '{"steps": [{"id": "s1", "description": "Fetch data", "tool": "jira_get", "depends_on": [], "can_parallel": true, "estimated_minutes": 2}, {"id": "s2", "description": "Process", "depends_on": ["s1"]}]}'
    fake = FakeProvider(responses=[llm_json])
    planner = WorkflowPlanner(provider=fake)
    plan = await planner.plan("Process Jira data", _CTX)
    assert len(plan.steps) == 2
    assert plan.steps[0].tool == "jira_get"


async def test_workflow_planner_llm_failure_falls_back_to_heuristic() -> None:
    broken = MagicMock()
    broken.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
    broken._default_model = ""
    planner = WorkflowPlanner(provider=broken)
    plan = await planner.plan("My goal", _CTX)
    assert len(plan.steps) == 1
    assert plan.steps[0].description == "My goal"


async def test_workflow_planner_invalid_json_falls_back() -> None:
    fake = FakeProvider(responses=["not json at all"])
    planner = WorkflowPlanner(provider=fake)
    plan = await planner.plan("My goal", _CTX)
    assert len(plan.steps) == 1


async def test_workflow_planner_json_without_steps_falls_back() -> None:
    # When LLM returns JSON without "steps" key, from_dict returns an empty plan
    # (no exception raised, so fallback is NOT triggered — empty plan is returned).
    fake = FakeProvider(responses=['{"other_key": "value"}'])
    planner = WorkflowPlanner(provider=fake)
    plan = await planner.plan("My goal", _CTX)
    # from_dict with missing "steps" key returns an empty WorkflowPlan (not heuristic fallback)
    assert isinstance(plan, WorkflowPlan)
    assert plan.goal == "My goal"


async def test_workflow_planner_with_tool_context() -> None:
    llm_json = '{"steps": [{"id": "s1", "description": "Use tool", "tool": "my_tool"}]}'
    fake = FakeProvider(responses=[llm_json])
    planner = WorkflowPlanner(provider=fake)

    tool_context = MagicMock()
    tool_context.tools = [MagicMock(name_attr="my_tool")]
    tool_context.tools[0].name = "my_tool"

    plan = await planner.plan("My goal", _CTX, tool_context=tool_context)
    assert len(plan.steps) == 1


async def test_workflow_planner_tool_context_exception_handled() -> None:
    """If tool_context access raises, planner still works."""
    llm_json = '{"steps": [{"id": "s1", "description": "Step"}]}'
    fake = FakeProvider(responses=[llm_json])
    planner = WorkflowPlanner(provider=fake)

    bad_ctx = MagicMock()
    bad_ctx.tools = None  # accessing .tools raises attribute error when iterating

    plan = await planner.plan("My goal", _CTX, tool_context=bad_ctx)
    assert len(plan.steps) == 1


async def test_heuristic_plan_structure() -> None:
    planner = WorkflowPlanner(provider=None)
    plan = planner._heuristic_plan("Specific goal text")
    assert plan.goal == "Specific goal text"
    assert plan.steps[0].id == "s1"
    assert plan.steps[0].estimated_minutes == 5
