"""Regression: tool/llm steps must not fabricate success when unwired.

Old bug: with no ``mcp_client`` a tool step returned ``{"_mock": True, ...}`` and
with no ``llm_provider`` an llm step returned ``"[FakeProvider: <id>]"`` — both
success-shaped, so a production run whose services failed to wire "completed"
having done nothing. Only an explicit test/simulation run (``is_test_run``) may
simulate.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.steps import StepServiceUnavailableError


def _state(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": "r1",
        "tenant_id": "t1",
        "inputs": {"text": "hi"},
        "step_outputs": {},
        "vars": {},
        "is_test_run": False,
        "mock_overrides": {},
    }
    base.update(kw)
    return base


async def test_tool_step_without_mcp_client_fails_in_real_run() -> None:
    from app.workflow.steps.tool_step import ToolStepNode

    node = ToolStepNode(StepDefinition(id="s1", type="tool", tool="slack.post"), ContextResolver())
    with pytest.raises(StepServiceUnavailableError, match="MCP client"):
        await node.execute(_state())


async def test_tool_step_without_mcp_client_simulates_only_in_test_run() -> None:
    from app.workflow.steps.tool_step import ToolStepNode

    node = ToolStepNode(StepDefinition(id="s1", type="tool", tool="slack.post"), ContextResolver())
    out = (await node.execute(_state(is_test_run=True)))["step_outputs"]["s1"]
    assert out["_mock"] is True and out["_simulated"] is True


async def test_llm_step_without_provider_fails_in_real_run() -> None:
    from app.workflow.steps.llm_step import LLMStepNode

    node = LLMStepNode(StepDefinition(id="l1", type="llm", prompt="{{inputs.text}}"), ContextResolver())
    with pytest.raises(StepServiceUnavailableError, match="LLM provider"):
        await node.execute(_state())


async def test_llm_step_without_provider_simulates_only_in_test_run() -> None:
    from app.workflow.steps.llm_step import LLMStepNode

    node = LLMStepNode(StepDefinition(id="l1", type="llm", prompt="x"), ContextResolver())
    out = (await node.execute(_state(is_test_run=True)))["step_outputs"]["l1"]
    assert out["_simulated"] is True


async def test_unwired_tool_step_fails_the_run_not_completes_it() -> None:
    """End to end: with on_failure=abort the run fails; default pause halts it —
    never a silent COMPLETE."""
    from app.workflow.compiler import WorkflowCompiler

    wf = WorkflowDefinition(
        name="x",
        steps=[StepDefinition(id="s1", type="tool", tool="t.x", on_failure="abort")],
    )
    compiled = WorkflowCompiler(ContextResolver()).compile(wf)
    with pytest.raises(StepServiceUnavailableError):
        await compiled.ainvoke(_state(), {"configurable": {"thread_id": "r1"}})
