"""MCPGOV-01: a workflow tool step governs the arguments it dispatches.

The step's input is normalised to the tool's schema (and validated) BEFORE the
policy rules and the risk gate — it used to be governed as written and rewritten
afterwards inside ``MCPClient.call_tool``, so a rule on ``arguments.amount`` never
saw an input that said ``amount_usd``.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent.tool_calls import prepare_tool_arguments
from app.governance import compliance_bundles, policy_rules
from app.mcp.client import ToolCallResult
from app.workflow.compiler import WorkflowCompiler
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.hitl_extension import HITLWorkflowGateway
from app.workflow.runner import WorkflowRunner
from app.workflow.state import WorkflowRunStatus
from tests.workflow.test_tool_step_risk_gate import _Store

pytestmark = pytest.mark.asyncio

LOOKUP_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string"}, "amount": {"type": "number"}},
    "required": ["order_id"],
}
AMOUNT_LIMIT = {
    "name": "amount-limit",
    "conditions": [{"field": "arguments.amount", "op": "gt", "value": 1000}],
    "logic": "AND",
    "action": "deny",
    "message": "Amounts above 1000 are not allowed",
}


class _MCP:
    """Normalises like the real client (``prepare_arguments_by_name``) and records."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def prepare_arguments_by_name(self, **kwargs: Any) -> Any:
        return prepare_tool_arguments(kwargs["arguments"], LOOKUP_SCHEMA)

    async def call_tool_by_name(self, **kwargs: Any) -> ToolCallResult:
        self.calls.append(kwargs)
        return ToolCallResult(tool_name=str(kwargs.get("tool_name")), success=True, output={})


@pytest.fixture(autouse=True)
def _governance(monkeypatch: pytest.MonkeyPatch) -> None:
    policy_rules.invalidate_policy_rules()

    async def _rules(db: Any, tenant_id: str) -> list[dict[str, Any]]:
        return [AMOUNT_LIMIT]

    async def _no_bundle(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(policy_rules, "load_active_policy_rules", _rules)
    monkeypatch.setattr(compliance_bundles, "bundle_hitl_requirement", _no_bundle)
    yield
    policy_rules.invalidate_policy_rules()


async def _run(step_input: dict[str, Any]) -> tuple[_MCP, _Store]:
    definition = WorkflowDefinition(
        id="wf-mcpgov",
        name="governed arguments",
        steps=[
            StepDefinition(id="call", type="tool", tool="get_order", input=step_input)
        ],
    )
    store = _Store()
    store.register_definition(definition)
    mcp = _MCP()
    compiler = WorkflowCompiler(
        context_resolver=ContextResolver(),
        run_store=store,
        hitl_workflow_gateway=HITLWorkflowGateway(),
        mcp_client=mcp,
        db_session_factory=object(),  # policy rules come from the stub above
    )
    runner = WorkflowRunner(compiler=compiler, run_store=store)
    await runner.run(workflow_id=definition.id, tenant_id="t-mcpgov-wf", inputs={})
    return mcp, store


async def test_policy_rule_on_amount_blocks_an_input_written_as_amount_usd() -> None:
    mcp, store = await _run({"order_id": "ORD-1", "amount_usd": 5000})

    assert mcp.calls == []
    assert store.last_status() == WorkflowRunStatus.FAILED
    assert "amount-limit" in str(store.statuses[-1][2].get("error") or "")


async def test_unknown_input_key_fails_the_step_without_a_call() -> None:
    mcp, store = await _run({"order_id": "ORD-1", "memo_x": "hi"})

    assert mcp.calls == []
    assert store.last_status() == WorkflowRunStatus.FAILED
    assert "memo_x" in str(store.statuses[-1][2].get("error") or "")


async def test_dispatches_the_normalised_input() -> None:
    mcp, store = await _run({"orderId": "ORD-1"})

    assert [c["arguments"] for c in mcp.calls] == [{"order_id": "ORD-1"}]
    assert store.last_status() == WorkflowRunStatus.COMPLETE
