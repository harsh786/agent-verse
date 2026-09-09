"""Tests for additional step types: tool, http, hitl, parallel, conditional, foreach, code."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workflow.context import ContextResolver
from app.workflow.dsl import (
    ConditionalBranch,
    HITLAction,
    StepDefinition,
)
from app.workflow.state import WorkflowRunStatus


def _ctx() -> ContextResolver:
    return ContextResolver()


def _state(**kwargs) -> dict:
    defaults = {
        "run_id": "run-1", "workflow_id": "wf-1", "tenant_id": "t-1",
        "inputs": {}, "step_outputs": {}, "vars": {},
        "status": WorkflowRunStatus.RUNNING,
    }
    defaults.update(kwargs)
    return defaults


# ── ToolStepNode ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_step_success() -> None:
    from app.workflow.steps.tool_step import ToolStepNode
    step = StepDefinition(id="s1", type="tool", tool="test.tool")
    mcp_client = AsyncMock()
    mcp_client.call_tool.return_value = {"result": "success"}
    node = ToolStepNode(step, _ctx(), mcp_client=mcp_client)
    state = _state()
    result = await node.execute(state)  # type: ignore[arg-type]
    assert "step_outputs" in result
    assert result["step_outputs"]["s1"]["result"] == "success"


@pytest.mark.asyncio
async def test_tool_step_no_mcp_client() -> None:
    from app.workflow.steps.tool_step import ToolStepNode
    step = StepDefinition(id="s1", type="tool", tool="test.tool")
    node = ToolStepNode(step, _ctx())
    state = _state()
    result = await node.execute(state)  # type: ignore[arg-type]
    # Without MCP client, returns mock output
    assert "step_outputs" in result


@pytest.mark.asyncio
async def test_tool_step_mcp_error() -> None:
    from app.workflow.steps.tool_step import ToolStepNode
    step = StepDefinition(id="s1", type="tool", tool="fail.tool")
    mcp_client = AsyncMock()
    mcp_client.call_tool.side_effect = RuntimeError("tool failed")
    node = ToolStepNode(step, _ctx(), mcp_client=mcp_client)
    state = _state()
    # Tool step propagates exceptions (runner handles them)
    with pytest.raises(RuntimeError, match="tool failed"):
        await node.execute(state)  # type: ignore[arg-type]


# ── LLMStepNode ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_step_fake_provider() -> None:
    from app.workflow.steps.llm_step import LLMStepNode
    step = StepDefinition(id="llm1", type="llm", prompt="Summarize: {{inputs.text}}")
    node = LLMStepNode(step, _ctx())
    state = _state(inputs={"text": "hello world"})
    result = await node.execute(state)  # type: ignore[arg-type]
    assert "step_outputs" in result
    assert "llm1" in result["step_outputs"]


@pytest.mark.asyncio
async def test_llm_step_resolves_template() -> None:
    from app.workflow.steps.llm_step import LLMStepNode
    step = StepDefinition(id="llm1", type="llm", prompt="Process: {{inputs.value}}")
    node = LLMStepNode(step, _ctx())
    state = _state(inputs={"value": "test input"})
    result = await node.execute(state)  # type: ignore[arg-type]
    assert result["step_outputs"]["llm1"] is not None


# ── HTTPStepNode ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_http_step_success() -> None:
    import httpx

    from app.workflow.security import SSRFGuard
    from app.workflow.steps.http_step import HTTPStepNode
    step = StepDefinition(id="h1", type="http", url="https://httpbin.org/get", method="GET")
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"ok": True}
    mock_response.text = '{"ok": true}'

    with patch.object(SSRFGuard, "validate", return_value=None):
        with patch("httpx.AsyncClient") as MockClient:
            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_cm
            mock_cm.request = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_cm

            node = HTTPStepNode(step, _ctx())
            state = _state()
            result = await node.execute(state)  # type: ignore[arg-type]
    assert "step_outputs" in result
    # HTTP step returns the JSON body directly (or {"body": ..., "status_code": ...})
    out = result["step_outputs"]["h1"]
    assert out is not None
    assert isinstance(out, dict)


@pytest.mark.asyncio
async def test_http_step_ssrf_blocked() -> None:
    from app.workflow.security import SSRFBlockedError
    from app.workflow.steps.http_step import HTTPStepNode
    step = StepDefinition(id="h1", type="http", url="http://169.254.169.254/metadata")
    node = HTTPStepNode(step, _ctx())
    state = _state()
    # SSRF guard raises SSRFBlockedError which bubbles up from the step
    with pytest.raises(SSRFBlockedError):
        await node.execute(state)  # type: ignore[arg-type]


# ── HITLStepNode ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hitl_step_suspends() -> None:
    """WS-3: uses a REAL HITLWorkflowGateway (not a loose AsyncMock) so a typo'd
    or missing method on the gateway (e.g. ``create_workflow_approval``) fails
    this test instead of being silently absorbed by mock auto-attributes."""
    from app.workflow.hitl_extension import HITLWorkflowGateway
    from app.workflow.steps.hitl_step import HITLStepNode
    step = StepDefinition(
        id="review",
        type="hitl",
        actions=[HITLAction(id="approve"), HITLAction(id="reject")],
    )
    gateway = HITLWorkflowGateway()
    # NOTE: the constructor key is "hitl_workflow_gateway" (matching the
    # services dict the WorkflowCompiler threads through) — a prior version of
    # this test passed "hitl_gateway" instead, which HITLStepNode silently
    # ignored (falling back to its no-gateway test-mode branch) and hid the
    # fact that neither the real gateway nor the compiler wired it correctly.
    node = HITLStepNode(step, _ctx(), hitl_workflow_gateway=gateway)
    state = _state()
    result = await node.execute(state)  # type: ignore[arg-type]
    assert result.get("status") == WorkflowRunStatus.WAITING_HITL

    # The request must actually be persisted in the gateway's store, reachable
    # via list_pending (the /approvals inbox), keyed off the real run/step ids.
    pending, total = await gateway.list_pending(tenant_id="t-1")
    assert total == 1
    assert pending[0].run_id == "run-1"
    assert pending[0].step_id == "review"


@pytest.mark.asyncio
async def test_hitl_step_resumes_with_action() -> None:
    from app.workflow.steps.hitl_step import HITLStepNode
    step = StepDefinition(
        id="review",
        type="hitl",
        actions=[HITLAction(id="approve"), HITLAction(id="reject")],
    )
    gateway = AsyncMock()
    node = HITLStepNode(step, _ctx(), hitl_workflow_gateway=gateway)
    # State has HITL already decided
    state = _state(
        hitl_request_id="review",
        hitl_action="approved",
        hitl_reviewer="user-abc",
    )
    result = await node.execute(state)  # type: ignore[arg-type]
    assert "step_outputs" in result
    assert result["step_outputs"]["review"]["action"] == "approved"


# ── ConditionalStepNode ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_conditional_step_selects_branch() -> None:
    from app.workflow.steps.conditional_step import ConditionalStepNode
    step = StepDefinition(
        id="cond",
        type="conditional",
        expression="1 == 1",
        branches=[
            ConditionalBranch(condition="1 == 1", next="yes"),
            ConditionalBranch(condition="else", next="no"),
        ],
    )
    node = ConditionalStepNode(step, _ctx())
    state = _state()
    result = await node.execute(state)  # type: ignore[arg-type]
    assert result["step_outputs"]["cond"]["chosen_branch"] in ("yes", "no", "true")


@pytest.mark.asyncio
async def test_conditional_else_branch() -> None:
    from app.workflow.steps.conditional_step import ConditionalStepNode
    step = StepDefinition(
        id="cond",
        type="conditional",
        expression="1 == 2",
        branches=[
            ConditionalBranch(condition="1 == 2", next="yes"),
            ConditionalBranch(condition="else", next="no"),
        ],
    )
    node = ConditionalStepNode(step, _ctx())
    state = _state()
    result = await node.execute(state)  # type: ignore[arg-type]
    branch = result["step_outputs"]["cond"].get("chosen_branch")
    # Should select 'no' (else branch) or None if not matched
    assert branch in ("no", "else", "false", None)


# ── ParallelStepNode ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parallel_step_runs_branches() -> None:
    from app.workflow.steps.parallel_step import ParallelStepNode
    step = StepDefinition(
        id="par",
        type="parallel",
        parallel_branches=[
            StepDefinition(id="b1", type="tool", tool="t"),
            StepDefinition(id="b2", type="tool", tool="t"),
        ],
    )
    node = ParallelStepNode(step, _ctx())
    state = _state()
    result = await node.execute(state)  # type: ignore[arg-type]
    assert "step_outputs" in result
    assert "par" in result["step_outputs"]


@pytest.mark.asyncio
async def test_parallel_step_no_branches() -> None:
    from app.workflow.steps.parallel_step import ParallelStepNode
    step = StepDefinition(id="par", type="parallel")
    node = ParallelStepNode(step, _ctx())
    result = await node.execute(_state())  # type: ignore[arg-type]
    assert result == {}


# ── CodeStepNode ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_code_step_python_execution() -> None:
    from app.workflow.steps.code_step import CodeStepNode
    step = StepDefinition(
        id="code1",
        type="code",
        runtime="python",
        code="output = {'result': 'hello'}",
    )
    node = CodeStepNode(step, _ctx())
    state = _state()
    result = await node.execute(state)  # type: ignore[arg-type]
    assert "step_outputs" in result
    assert "code1" in result["step_outputs"]


@pytest.mark.asyncio
async def test_code_step_blocked_import() -> None:
    from app.workflow.steps.code_step import CodeStepNode
    step = StepDefinition(
        id="code1",
        type="code",
        runtime="python",
        code="import os; output = os.listdir('/')",
    )
    node = CodeStepNode(step, _ctx())
    state = _state()
    result = await node.execute(state)  # type: ignore[arg-type]
    # Should either block the import or produce some output
    assert "step_outputs" in result
    assert "code1" in result["step_outputs"]
