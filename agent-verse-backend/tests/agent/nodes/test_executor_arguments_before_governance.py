"""MCPGOV-01: the executor governs the arguments it dispatches.

The LLM's argument names are normalised to the tool's schema (and validated)
BEFORE the policy rules, grants, risk gate and human approval run. They used to
be governed raw and rewritten later inside ``MCPClient.call_tool``: a tenant rule
"deny payouts above 1000" (``arguments.amount gt 1000``) never matched a call that
said ``amount_usd``, a human approved it, and the connector received ``amount``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.agent.tool_context import ToolContext, ToolRef
from app.governance import compliance_bundles, policy_rules
from app.governance.hitl import ApprovalStatus
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-mcpgov-ex", plan=PlanTier.ENTERPRISE, api_key_id="k")

PAYOUT_SCHEMA = {
    "type": "object",
    "properties": {"amount": {"type": "number"}, "destination": {"type": "string"}},
    "required": ["amount", "destination"],
}
LOOKUP_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string"}},
    "required": ["order_id"],
}
PAYOUT_LIMIT = {
    "name": "payout-limit",
    "conditions": [
        {"field": "tool_name", "op": "contains", "value": "payout"},
        {"field": "arguments.amount", "op": "gt", "value": 1000},
    ],
    "logic": "AND",
    "action": "deny",
    "message": "Payouts above 1000 are not allowed",
}


class _Human:
    def __init__(self) -> None:
        self.filed: list[str] = []
        self._redis: Any = None

    async def request_approval_async(self, *, action: str, **_kw: Any) -> str:
        self.filed.append(action)
        return f"req-{len(self.filed)}"

    async def wait_for_approval(self, request_id: str, **_kw: Any) -> ApprovalStatus:
        return ApprovalStatus.APPROVED

    def list_pending(self, **_kw: Any) -> list[Any]:
        return []


class _MCP:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call_tool(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        class Result:
            success = True
            output = {"ok": True}
            error = ""

        return Result()


@pytest.fixture(autouse=True)
def _governance(monkeypatch: pytest.MonkeyPatch) -> None:
    policy_rules.invalidate_policy_rules()

    async def _rules(db: Any, tenant_id: str) -> list[dict[str, Any]]:
        return [PAYOUT_LIMIT]

    async def _no_bundle(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(policy_rules, "load_active_policy_rules", _rules)
    monkeypatch.setattr(compliance_bundles, "bundle_hitl_requirement", _no_bundle)
    yield
    policy_rules.invalidate_policy_rules()


def _graph(executor: FakeProvider, human: _Human, mcp: _MCP) -> AgentGraph:
    graph = AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=executor,
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        hitl_gateway=human,
        mcp_client=mcp,
        autonomy_mode="supervised",
    )
    graph._db_session_factory = object()  # policy rules are loaded via the stub above
    graph._checkpoints_enabled = False
    return graph


def _state() -> AgentState:
    state = AgentState(goal="pay the vendor", tenant_ctx=T)
    state.goal_id = "g-mcpgov"
    state.context["tool_context"] = ToolContext(
        connectors=[],
        tools=[
            ToolRef(
                server_id="pay-1",
                server_name="payouts",
                name="stripe_create_payout",
                description="send a payout",
                input_schema=PAYOUT_SCHEMA,
            ),
            ToolRef(
                server_id="orders-1",
                server_name="orders",
                name="orders_lookup",
                description="look up an order",
                input_schema=LOOKUP_SCHEMA,
            ),
        ],
    )
    return state


def _call(tool: str, args: dict[str, Any]) -> str:
    return json.dumps({"tool": tool, "arguments": args})


def _tool_approvals(human: _Human) -> list[str]:
    """Approvals filed for the payout CALL (a risky step text files its own)."""
    return [a for a in human.filed if "stripe_create_payout" in a]


async def _run_step(graph: AgentGraph, state: AgentState, step: str) -> str:
    state.steps.append(StepResult(description=step, status=StepStatus.RUNNING))
    return await graph._execute_step(step, state, T)


async def test_policy_rule_on_amount_blocks_a_call_submitted_as_amount_usd() -> None:
    human, mcp = _Human(), _MCP()
    call = _call("stripe_create_payout", {"amount_usd": 5000, "destination": "acct_x"})
    graph = _graph(FakeProvider(responses=[call]), human, mcp)

    out = await _run_step(graph, _state(), "Send the vendor payout")

    assert mcp.calls == []
    assert _tool_approvals(human) == []  # denied by policy before the call's approval
    assert "payout-limit" in out


async def test_unknown_argument_is_rejected_before_approval() -> None:
    human, mcp = _Human(), _MCP()
    call = _call(
        "stripe_create_payout", {"amount": 10, "destination": "acct_x", "memo_x": "hi"}
    )
    graph = _graph(FakeProvider(responses=[call]), human, mcp)

    out = await _run_step(graph, _state(), "Send the vendor payout")

    assert mcp.calls == []
    assert _tool_approvals(human) == []
    assert "memo_x" in out


async def test_dispatched_arguments_are_the_normalised_governed_ones() -> None:
    human, mcp = _Human(), _MCP()
    call = _call("orders_lookup", {"orderId": "ORD-7"})
    graph = _graph(FakeProvider(responses=[call]), human, mcp)

    await _run_step(graph, _state(), "Look up order ORD-7")

    assert [c["arguments"] for c in mcp.calls] == [{"order_id": "ORD-7"}]


async def test_parallel_extra_call_is_governed_on_normalised_arguments() -> None:
    human, mcp = _Human(), _MCP()
    graph = _graph(FakeProvider(responses=[]), human, mcp)
    state = _state()
    state.steps.append(StepResult(description="pay", status=StepStatus.RUNNING))

    results = await graph._dispatch_parallel_extra_tool_calls(
        [{"name": "stripe_create_payout",
          "input": {"amount_usd": 5000, "destination": "acct_x"}}],
        "Send the vendor payout",
        state,
        T,
        {"stripe_create_payout", "orders_lookup"},
    )

    assert mcp.calls == []
    assert _tool_approvals(human) == []
    assert "payout-limit" in results[0][1]
