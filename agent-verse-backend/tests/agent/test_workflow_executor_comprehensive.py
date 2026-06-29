"""Comprehensive tests for app/agent/workflow_executor.py — targets 90%+ statement coverage."""
from __future__ import annotations

from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.tool_context import ToolContext, ToolRef
from app.agent.workflow_executor import (
    WorkflowExecutor,
    _arguments_for_step,
    _summarize_inputs,
    _INTENT_TOOL_TOKENS,
)
from app.agent.workflow_planner import (
    WorkflowPlan,
    WorkflowStep,
    _StaticWorkflowPlan,
    _StaticWorkflowStep,
    build_static_workflow,
)
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-we", plan=PlanTier.ENTERPRISE, api_key_id="key-we")


# ── _arguments_for_step ───────────────────────────────────────────────────────

def _make_static_step(intent: str, input_from=None) -> _StaticWorkflowStep:
    return _StaticWorkflowStep(
        step_id="step_1",
        connector_name="test",
        agent_id=None,
        intent=intent,
        input_from=input_from or [],
        requires_approval=False,
    )


def test_arguments_for_fetch_open_issues() -> None:
    step = _make_static_step("fetch_open_issues")
    args = _arguments_for_step(step, {})
    assert "jql" in args


def test_arguments_for_create_summary_page() -> None:
    step = _make_static_step("create_summary_page", input_from=["step_0"])
    outputs = {"step_0": "Previous output"}
    args = _arguments_for_step(step, outputs)
    assert "title" in args
    assert "content" in args


def test_arguments_for_send_summary_email() -> None:
    step = _make_static_step("send_summary_email", input_from=["step_0"])
    args = _arguments_for_step(step, {"step_0": "Email content"})
    assert "subject" in args
    assert "body" in args


def test_arguments_for_browser_automation() -> None:
    step = _make_static_step("browser_automation")
    args = _arguments_for_step(step, {})
    assert "instruction" in args


def test_arguments_for_unknown_intent_returns_empty() -> None:
    step = _make_static_step("unknown_intent")
    args = _arguments_for_step(step, {})
    assert args == {}


# ── _summarize_inputs ─────────────────────────────────────────────────────────

def test_summarize_inputs_empty_inputs() -> None:
    step = _make_static_step("send_summary_email", input_from=[])
    result = _summarize_inputs(step, {})
    assert "Workflow inputs" in result


def test_summarize_inputs_with_data() -> None:
    step = _make_static_step("create_summary_page", input_from=["step_1"])
    result = _summarize_inputs(step, {"step_1": {"data": "issue list"}})
    assert "step_1" in result


# ── WorkflowExecutor._execute_step ────────────────────────────────────────────

async def test_execute_step_no_provider_no_tool_returns_stub() -> None:
    executor = WorkflowExecutor(provider=None, mcp_client=None)
    step = WorkflowStep(id="s1", description="Do something")
    result = await executor._execute_step(step, _CTX, prior_results={})
    assert result["status"] == "complete"
    assert "Completed:" in result["output"]


async def test_execute_step_with_provider_uses_llm() -> None:
    fake = FakeProvider(responses=["LLM completed the step"])
    executor = WorkflowExecutor(provider=fake, mcp_client=None)
    step = WorkflowStep(id="s1", description="Analyze data")
    result = await executor._execute_step(step, _CTX, prior_results={})
    assert result["status"] == "complete"
    assert result["output"] == "LLM completed the step"


async def test_execute_step_with_tool_and_mcp_client() -> None:
    mcp = MagicMock()
    mcp.call_tool = AsyncMock(return_value="Tool result")
    executor = WorkflowExecutor(provider=None, mcp_client=mcp)
    step = WorkflowStep(id="s1", description="Fetch issues", tool="jira_search")
    result = await executor._execute_step(step, _CTX, prior_results={})
    assert result["status"] == "complete"
    assert result["tool"] == "jira_search"


async def test_execute_step_tool_failure_falls_back_to_llm() -> None:
    mcp = MagicMock()
    mcp.call_tool = AsyncMock(side_effect=RuntimeError("tool failed"))
    fake = FakeProvider(responses=["LLM fallback result"])
    executor = WorkflowExecutor(provider=fake, mcp_client=mcp)
    step = WorkflowStep(id="s1", description="Step with tool", tool="failing_tool")
    result = await executor._execute_step(step, _CTX, prior_results={})
    assert result["status"] == "complete"
    assert result["output"] == "LLM fallback result"


async def test_execute_step_tool_failure_no_provider_returns_stub() -> None:
    mcp = MagicMock()
    mcp.call_tool = AsyncMock(side_effect=RuntimeError("tool failed"))
    executor = WorkflowExecutor(provider=None, mcp_client=mcp)
    step = WorkflowStep(id="s1", description="Step", tool="failing_tool")
    result = await executor._execute_step(step, _CTX, prior_results={})
    # Falls to stub
    assert result["status"] == "complete"


async def test_execute_step_with_prior_context() -> None:
    fake = FakeProvider(responses=["Result using context"])
    executor = WorkflowExecutor(provider=fake)
    step = WorkflowStep(id="s2", description="Use prior data", depends_on=["s1"])
    prior_results = {"s1": {"output": "Previous step result"}}
    result = await executor._execute_step(step, _CTX, prior_results=prior_results)
    assert result["status"] == "complete"


async def test_execute_step_exception_returns_failed() -> None:
    fake = MagicMock()
    fake.complete = AsyncMock(side_effect=RuntimeError("provider exploded"))
    fake._default_model = ""
    executor = WorkflowExecutor(provider=fake)
    step = WorkflowStep(id="s1", description="Failing step")
    result = await executor._execute_step(step, _CTX, prior_results={})
    assert result["status"] == "failed"
    assert "exploded" in result["error"]


# ── WorkflowExecutor.execute ─────────────────────────────────────────────────

async def test_execute_single_step_success() -> None:
    fake = FakeProvider(responses=["Done"])
    executor = WorkflowExecutor(provider=fake)
    plan = WorkflowPlan(goal="G", steps=[WorkflowStep(id="s1", description="Single step")])
    result = await executor.execute(plan, _CTX)
    assert result["status"] == "complete"
    assert result["steps_executed"] == 1


async def test_execute_multiple_independent_steps() -> None:
    fake = FakeProvider(responses=["R1", "R2", "R3"])
    executor = WorkflowExecutor(provider=fake)
    plan = WorkflowPlan(goal="G", steps=[
        WorkflowStep(id="s1", description="A"),
        WorkflowStep(id="s2", description="B"),
        WorkflowStep(id="s3", description="C"),
    ])
    result = await executor.execute(plan, _CTX)
    assert result["status"] == "complete"
    assert result["steps_executed"] == 3
    assert result["waves"] == 1  # all independent → 1 wave


async def test_execute_chained_steps_sequential_waves() -> None:
    fake = FakeProvider(responses=["R1", "R2"])
    executor = WorkflowExecutor(provider=fake)
    plan = WorkflowPlan(goal="G", steps=[
        WorkflowStep(id="s1", description="Step 1"),
        WorkflowStep(id="s2", description="Step 2", depends_on=["s1"]),
    ])
    result = await executor.execute(plan, _CTX)
    assert result["status"] == "complete"
    assert result["waves"] == 2


async def test_execute_step_failure_returns_failed_dict() -> None:
    broken = MagicMock()
    broken.complete = AsyncMock(side_effect=RuntimeError("broken"))
    broken._default_model = ""
    executor = WorkflowExecutor(provider=broken)
    plan = WorkflowPlan(goal="G", steps=[WorkflowStep(id="s1", description="Fail")])
    result = await executor.execute(plan, _CTX)
    assert result["status"] == "failed"


async def test_execute_parallel_wave_exception_caught() -> None:
    """Exception during parallel execution is caught in gather return_exceptions."""
    broken = MagicMock()
    broken.complete = AsyncMock(side_effect=RuntimeError("parallel fail"))
    broken._default_model = ""
    executor = WorkflowExecutor(provider=broken)
    plan = WorkflowPlan(goal="G", steps=[
        WorkflowStep(id="s1", description="A"),
        WorkflowStep(id="s2", description="B"),
    ])
    # Both fail in parallel → should return failed
    result = await executor.execute(plan, _CTX)
    assert result["status"] == "failed"


async def test_execute_empty_plan_returns_complete() -> None:
    executor = WorkflowExecutor()
    plan = WorkflowPlan(goal="G", steps=[])
    result = await executor.execute(plan, _CTX)
    assert result["status"] == "complete"
    assert result["steps_executed"] == 0


async def test_execute_summary_from_completed_steps() -> None:
    fake = FakeProvider(responses=["Output A", "Output B"])
    executor = WorkflowExecutor(provider=fake)
    plan = WorkflowPlan(goal="G", steps=[
        WorkflowStep(id="s1", description="A"),
        WorkflowStep(id="s2", description="B"),
    ])
    result = await executor.execute(plan, _CTX)
    assert "Output A" in result["summary"] or "Output B" in result["summary"]


# ── WorkflowExecutor.run (legacy sequential API) ─────────────────────────────

async def test_run_legacy_emits_workflow_planned() -> None:
    events: list[dict] = []

    async def cb(evt: dict) -> None:
        events.append(evt)

    executor = WorkflowExecutor(provider=None, mcp_client=None)
    plan = build_static_workflow("")  # empty plan, no steps
    await executor.run(
        plan=plan, goal="Test", tenant_ctx=_CTX,
        tool_context=None, event_callback=cb,
    )
    assert any(e.get("type") == "workflow_planned" for e in events)


async def test_run_legacy_step_requires_approval_returns_planned() -> None:
    events: list[dict] = []

    async def cb(evt: dict) -> None:
        events.append(evt)

    plan = _StaticWorkflowPlan(steps=[
        _StaticWorkflowStep(
            step_id="step_1",
            connector_name="jira",
            agent_id=None,
            intent="fetch_open_issues",
            input_from=[],
            requires_approval=True,
        )
    ])
    executor = WorkflowExecutor(provider=None, mcp_client=None)
    await executor.run(
        plan=plan, goal="Test", tenant_ctx=_CTX,
        tool_context=None, event_callback=cb,
    )
    # Should emit step_complete with planned_not_executed
    step_done = [e for e in events if e.get("type") == "workflow_step_complete"]
    assert len(step_done) == 1
    assert step_done[0]["output"]["status"] == "planned_not_executed"


async def test_run_legacy_no_matching_tool_returns_planned() -> None:
    events: list[dict] = []

    async def cb(evt: dict) -> None:
        events.append(evt)

    plan = _StaticWorkflowPlan(steps=[
        _StaticWorkflowStep(
            step_id="step_1",
            connector_name="jira",
            agent_id=None,
            intent="fetch_open_issues",
            input_from=[],
            requires_approval=False,
        )
    ])
    executor = WorkflowExecutor(provider=None, mcp_client=None)
    await executor.run(
        plan=plan, goal="Test", tenant_ctx=_CTX,
        tool_context=None, event_callback=cb,
    )
    step_done = [e for e in events if e.get("type") == "workflow_step_complete"]
    assert step_done[0]["output"]["reason"] == "no_matching_connector_tool"


async def test_run_legacy_with_matching_tool_and_mcp_success() -> None:
    events: list[dict] = []

    async def cb(evt: dict) -> None:
        events.append(evt)

    tool_result = MagicMock()
    tool_result.success = True
    tool_result.output = "Issues fetched"
    tool_result.error = ""

    mcp = MagicMock()
    mcp.call_tool = AsyncMock(return_value=tool_result)

    jira_tool = ToolRef(
        server_id="jira-srv",
        server_name="Jira",
        name="search",
        description="jira search issue",
        input_schema={},
    )
    tool_context = ToolContext(connectors=[], tools=[jira_tool])

    plan = _StaticWorkflowPlan(steps=[
        _StaticWorkflowStep(
            step_id="step_1",
            connector_name="jira",
            agent_id=None,
            intent="fetch_open_issues",
            input_from=[],
            requires_approval=False,
        )
    ])
    executor = WorkflowExecutor(provider=None, mcp_client=mcp)
    await executor.run(
        plan=plan, goal="Test", tenant_ctx=_CTX,
        tool_context=tool_context, event_callback=cb,
    )
    step_done = [e for e in events if e.get("type") == "workflow_step_complete"]
    assert step_done[0]["output"]["status"] == "executed"


async def test_run_legacy_mcp_tool_failure_returns_tool_call_failed() -> None:
    events: list[dict] = []

    async def cb(evt: dict) -> None:
        events.append(evt)

    tool_result = MagicMock()
    tool_result.success = False
    tool_result.output = ""
    tool_result.error = "connection refused"

    mcp = MagicMock()
    mcp.call_tool = AsyncMock(return_value=tool_result)

    jira_tool = ToolRef(
        server_id="jira-srv",
        server_name="Jira",
        name="issue",
        description="jira search issue",
        input_schema={},
    )
    tool_context = ToolContext(connectors=[], tools=[jira_tool])

    plan = _StaticWorkflowPlan(steps=[
        _StaticWorkflowStep(
            step_id="step_1",
            connector_name="jira",
            agent_id=None,
            intent="fetch_open_issues",
            input_from=[],
            requires_approval=False,
        )
    ])
    executor = WorkflowExecutor(provider=None, mcp_client=mcp)
    await executor.run(
        plan=plan, goal="Test", tenant_ctx=_CTX,
        tool_context=tool_context, event_callback=cb,
    )
    step_done = [e for e in events if e.get("type") == "workflow_step_complete"]
    assert step_done[0]["output"]["status"] == "tool_call_failed"


# ── WorkflowExecutor._find_matching_tool ─────────────────────────────────────

def test_find_matching_tool_no_tool_context() -> None:
    executor = WorkflowExecutor()
    step = _make_static_step("fetch_open_issues")
    assert executor._find_matching_tool(step, None) is None


def test_find_matching_tool_no_connector_name() -> None:
    executor = WorkflowExecutor()
    step = _StaticWorkflowStep(
        step_id="s1",
        connector_name=None,
        agent_id=None,
        intent="fetch_open_issues",
        input_from=[],
        requires_approval=False,
    )
    ctx = ToolContext(connectors=[], tools=[])
    assert executor._find_matching_tool(step, ctx) is None


def test_find_matching_tool_connector_not_in_any_tool() -> None:
    executor = WorkflowExecutor()
    step = _make_static_step("fetch_open_issues")
    tool = ToolRef(server_id="slack", server_name="Slack", name="send_msg", description="slack send message", input_schema={})
    ctx = ToolContext(connectors=[], tools=[tool])
    assert executor._find_matching_tool(step, ctx) is None


def test_find_matching_tool_intent_tokens_match() -> None:
    executor = WorkflowExecutor()
    # Use "email" as connector_name to match the tool's server info
    step = _StaticWorkflowStep(
        step_id="step_1",
        connector_name="email",
        agent_id=None,
        intent="send_summary_email",
        input_from=[],
        requires_approval=False,
    )
    # Tool must contain "email" in its haystack AND one of the intent tokens
    tool = ToolRef(
        server_id="email-srv",
        server_name="Email",
        name="send_email",
        description="send email mail message",
        input_schema={},
    )
    ctx = ToolContext(connectors=[], tools=[tool])
    found = executor._find_matching_tool(step, ctx)
    assert found is not None
