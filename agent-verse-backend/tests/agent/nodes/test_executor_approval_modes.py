"""CORE-01: an approval-required step never runs without an APPROVED decision.

Outside supervised mode nothing waits for an approval decision, so the four
approval gates in the executor (action-safety HITL_REQUIRED, tenant policy
REQUIRE_APPROVAL, the gate-7 high-risk keyword gate, and the write_high tool
gate) used to *file* an approval request and then either run the step anyway
(action-safety, gate 7) or deny it while leaving the request pending forever
(policy, write_high tool). Approving or rejecting those requests changed
nothing.

The rule now: outside supervised mode an approval-required step is denied with
an honest error and no approval request is filed. In supervised mode the gate
waits for the decision exactly as before — and when no approval gateway is
wired at all, the step is denied instead of silently running.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.graph import AgentGraph
from app.agent.nodes._helpers import _is_high_risk_step
from app.agent.state import AgentState, StepResult, StepStatus
from app.agent.tool_context import ToolContext, ToolRef
from app.governance.hitl import ApprovalStatus, HITLGateway
from app.governance.policies import PolicyResult
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="core01-t1", plan=PlanTier.ENTERPRISE, api_key_id="c01")

NON_SUPERVISED = ["bounded-autonomous", "fully-autonomous"]


class _RecordingMCPClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def call_tool(self, **kwargs: object) -> object:
        self.calls.append(kwargs)

        class Result:
            success = True
            output = {"updated": True}
            error = ""

        return Result()


def _graph(executor: FakeProvider, **kwargs: object) -> AgentGraph:
    return AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=executor,
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        **kwargs,
    )


def _state(step_desc: str) -> AgentState:
    state = AgentState(goal="goal", tenant_ctx=T)
    state.steps.append(StepResult(description=step_desc, status=StepStatus.RUNNING))
    return state


def _spy_gateway() -> HITLGateway:
    hitl = HITLGateway()
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.APPROVED)  # type: ignore[method-assign]
    return hitl


def _collect(graph: AgentGraph) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []

    async def _cb(event: dict[str, object]) -> None:
        events.append(event)

    graph._event_callback = _cb  # type: ignore[assignment]
    return events


# ── action-safety HITL_REQUIRED ─────────────────────────────────────────────


@pytest.mark.parametrize("mode", NON_SUPERVISED)
async def test_action_safety_hitl_required_is_denied_outside_supervised(mode: str) -> None:
    executor = FakeProvider(responses=["must not run"])
    hitl = _spy_gateway()
    graph = _graph(executor, hitl_gateway=hitl, autonomy_mode=mode)
    events = _collect(graph)
    state = _state("perform a critical action")
    state.context["_risk_level"] = "critical"

    with pytest.raises(PermissionError, match="supervised"):
        await graph._execute_step("perform a critical action", state, T)

    assert executor.call_history == []
    assert hitl.list_pending(tenant_ctx=T) == []
    hitl.wait_for_approval.assert_not_awaited()  # type: ignore[attr-defined]
    assert not any(e.get("type") == "waiting_approval" for e in events)


# ── gate 7: high-risk keyword step ──────────────────────────────────────────


@pytest.mark.parametrize("mode", NON_SUPERVISED)
async def test_high_risk_step_is_denied_outside_supervised(mode: str) -> None:
    executor = FakeProvider(responses=["deployed"])
    hitl = _spy_gateway()
    graph = _graph(executor, hitl_gateway=hitl, autonomy_mode=mode)
    step = "deploy the service to production"

    with pytest.raises(PermissionError, match="supervised"):
        await graph._execute_step(step, _state(step), T)

    assert executor.call_history == []
    assert hitl.list_pending(tenant_ctx=T) == []


@pytest.mark.parametrize("mode", ["supervised", *NON_SUPERVISED])
async def test_high_risk_step_without_gateway_is_denied(mode: str) -> None:
    """No approval gateway means nobody can approve — the step must not run."""
    executor = FakeProvider(responses=["deleted"])
    graph = _graph(executor, hitl_gateway=None, autonomy_mode=mode)
    step = "delete the customer table"

    with pytest.raises(PermissionError, match="approval"):
        await graph._execute_step(step, _state(step), T)

    assert executor.call_history == []


async def test_high_risk_step_supervised_still_waits_and_runs_on_approval() -> None:
    executor = FakeProvider(responses=["deployed"])
    hitl = _spy_gateway()
    graph = _graph(executor, hitl_gateway=hitl, autonomy_mode="supervised")
    step = "deploy the service to production"

    output = await graph._execute_step(step, _state(step), T)

    hitl.wait_for_approval.assert_awaited_once()  # type: ignore[attr-defined]
    assert output == "deployed"


# ── tenant policy REQUIRE_APPROVAL ──────────────────────────────────────────


@pytest.mark.parametrize("mode", NON_SUPERVISED)
async def test_policy_approval_is_denied_without_orphan_request(mode: str) -> None:
    policy = MagicMock()
    policy.evaluate.return_value = PolicyResult.REQUIRE_APPROVAL
    executor = FakeProvider(responses=["must not run"])
    hitl = _spy_gateway()
    graph = _graph(executor, policy_engine=policy, hitl_gateway=hitl, autonomy_mode=mode)

    with pytest.raises(PermissionError, match="supervised"):
        await graph._execute_step("do the thing", _state("do the thing"), T)

    assert executor.call_history == []
    assert hitl.list_pending(tenant_ctx=T) == []


# ── write_high tool gate ────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", NON_SUPERVISED)
async def test_write_high_tool_is_not_dispatched_and_files_no_request(mode: str) -> None:
    executor = FakeProvider(
        responses=['{"tool": "jira_update_issue", "arguments": {"issue_key": "BAU-1"}}']
    )
    mcp = _RecordingMCPClient()
    hitl = _spy_gateway()
    graph = _graph(executor, mcp_client=mcp, hitl_gateway=hitl, autonomy_mode=mode)
    events = _collect(graph)
    state = _state("update Jira issue")
    state.context["tool_context"] = ToolContext(
        connectors=[],
        tools=[
            ToolRef(
                server_id="jira",
                server_name="Jira",
                name="jira_update_issue",
                description="update an issue",
                input_schema={},
            )
        ],
    )

    output = await graph._execute_step("update Jira issue", state, T)

    assert mcp.calls == []
    assert "requires approval" in output
    assert hitl.list_pending(tenant_ctx=T) == []
    types = {e.get("type") for e in events}
    assert "waiting_approval" not in types
    assert "tool_call_pending_approval" not in types
    assert "tool_call_failed" in types


# ── gate-7 vocabulary: whole words, not substrings ──────────────────────────


@pytest.mark.parametrize(
    "step",
    [
        "deploy the api",
        "Deployment of the new build",
        "delete old records",
        "call github_delete_repo for the fork",
        "run deleteUser on the stale account",
        "drop the staging table",
        "restart prod",
        "update the production config",
        "destroy the cluster",
        "wipe the cache",
        "truncate the logs table",
        "rm -rf build/",
    ],
)
def test_high_risk_vocabulary_matches_destructive_steps(step: str) -> None:
    assert _is_high_risk_step(step)


@pytest.mark.parametrize(
    "step",
    [
        "summarize the product reviews",
        "list all products in the catalogue",
        "measure team productivity",
        "open the dropdown menu",
        "format the document and perform analysis",
    ],
)
def test_high_risk_vocabulary_ignores_substring_false_positives(step: str) -> None:
    assert not _is_high_risk_step(step)
