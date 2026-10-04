"""Every tool call in one executor turn passes the same gate as the first.

With a parallel tool strategy, the 2nd+ structured tool calls of a turn went
through ``_dispatch_parallel_extra_tool_calls``, which skipped the per-agent
permission rules, grants, the tool policy engine, the tool-argument
guardrails, the supervised HITL wait and the tool-call budget that the first
call gets. A model could therefore run an ungranted / denied / unapproved tool
simply by emitting it as the second call of a turn.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.agent.tool_context import ToolContext, ToolRef
from app.governance.agent_permissions import AgentPermissionRule
from app.governance.grants.models import Grant
from app.governance.grants.store import InMemoryGrantStore
from app.governance.hitl import ApprovalStatus, HITLGateway
from app.governance.permissions import ActionLevel
from app.governance.policies import PolicyResult
from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="par-gate-t1", plan=PlanTier.ENTERPRISE, api_key_id="pk1")


class _MultiToolCallProvider(FakeProvider):
    def __init__(self, tool_calls: list[dict[str, Any]]) -> None:
        super().__init__(responses=["ignored"])
        self._tool_calls = tool_calls

    async def stream_tokens(self, request, on_token):  # type: ignore[override]
        await on_token("ok")
        return CompletionResponse(
            content="ok", model=request.model, input_tokens=5, output_tokens=1,
            tool_calls=self._tool_calls,
        )

    async def complete(self, request):  # type: ignore[override]
        return CompletionResponse(
            content="ok", model=request.model, input_tokens=5, output_tokens=1,
            tool_calls=self._tool_calls,
        )


class _RecordingMCP:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call_tool(self, **kwargs: Any) -> Any:
        self.calls.append(str(kwargs["tool_name"]))
        return SimpleNamespace(success=True, output={"ok": kwargs["tool_name"]}, error="")


def _tools(*specs: tuple[str, str]) -> ToolContext:
    return ToolContext(
        connectors=[],
        tools=[
            ToolRef(server_id=n, server_name=s, name=n, description=n, input_schema={})
            for n, s in specs
        ],
    )


def _setup(calls: list[str], extra_tools: tuple[tuple[str, str], ...] = (), **kwargs: Any):
    mcp = _RecordingMCP()
    graph = AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=_MultiToolCallProvider([{"name": n, "input": {}} for n in calls]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        mcp_client=mcp,
        **kwargs,
    )
    state = AgentState(goal="goal", tenant_ctx=T)
    state.steps.append(StepResult(description="gather data", status=StepStatus.RUNNING))
    state.context["tool_context"] = _tools(
        ("search_a", "Custom"), ("search_b", "Custom"), ("search_c", "Custom"), *extra_tools
    )
    state.context["_execution_strategy"] = SimpleNamespace(
        tool_mode=SimpleNamespace(value="parallel")
    )
    return graph, state, mcp


async def test_second_call_without_a_grant_does_not_run() -> None:
    store = InMemoryGrantStore()
    now = datetime.now(UTC)
    await store.issue(
        Grant(grant_id="g1", tenant_id=T.tenant_id, grantor="o", grantee_agent_id="",
              scopes=("search_a",), not_before=now - timedelta(minutes=1),
              expires_at=now + timedelta(hours=1))
    )
    graph, state, mcp = _setup(["search_a", "search_b"], grant_store=store, enforce_grants=True)

    out = await graph._execute_step("gather data", state, T)

    assert mcp.calls == ["search_a"]
    assert "search_b" in out and "not granted" in out  # reported, not silently dropped


async def test_second_call_denied_by_agent_permission_does_not_run() -> None:
    graph, state, mcp = _setup(["search_a", "search_b"])
    graph._agent_id = "agent-1"
    graph._db_session_factory = object()
    rules = (AgentPermissionRule(tool_name="search_b", level=ActionLevel.DENY),)
    with (
        patch(
            "app.governance.agent_permissions.load_agent_permissions",
            AsyncMock(return_value=rules),
        ),
        # The fake DB has no policy-as-code rules (POL-01 reads them too).
        patch(
            "app.governance.policy_rules.load_active_policy_rules", AsyncMock(return_value=[])
        ),
        patch(
            "app.governance.compliance_bundles.bundle_hitl_requirement",
            AsyncMock(return_value=None),
        ),
    ):
        out = await graph._execute_step("gather data", state, T)

    assert mcp.calls == ["search_a"]
    assert "search_b" in out and "not permitted" in out


async def test_second_call_denied_by_tool_policy_does_not_run() -> None:
    policy = MagicMock()
    policy.evaluate.side_effect = lambda tool_name, tenant_ctx: (
        PolicyResult.DENY if tool_name == "search_b" else PolicyResult.ALLOW
    )
    graph, state, mcp = _setup(["search_a", "search_b"], policy_engine=policy)

    out = await graph._execute_step("gather data", state, T)

    assert mcp.calls == ["search_a"]
    assert "search_b" in out and "denied" in out.lower()


async def test_first_call_denied_by_tool_policy_does_not_run_either() -> None:
    """The primary call is gated by the policy on the tool it actually calls
    (the step-level check only sees a name guessed from the step text)."""
    policy = MagicMock()
    policy.evaluate.side_effect = lambda tool_name, tenant_ctx: (
        PolicyResult.DENY if tool_name == "search_a" else PolicyResult.ALLOW
    )
    graph, state, mcp = _setup(["search_a"], policy_engine=policy)

    out = await graph._execute_step("gather data", state, T)

    assert mcp.calls == []
    assert "search_a" in out and "denied" in out.lower()


async def test_second_call_blocked_by_argument_guardrail_fails_like_the_first() -> None:
    engine = MagicMock()

    async def _evaluate_tool_args(tool_name: str, arguments: Any, context: Any) -> Any:
        if tool_name == "search_b":
            return SimpleNamespace(
                allowed=False, violations=[SimpleNamespace(matched_pattern="secret")]
            )
        return SimpleNamespace(allowed=True, violations=[])

    engine.evaluate_tool_args = _evaluate_tool_args
    graph, state, mcp = _setup(["search_a", "search_b"])
    graph._app_state = SimpleNamespace(guardrail_engine=engine)

    with pytest.raises(PermissionError, match="Guardrail blocked tool call 'search_b'"):
        await graph._execute_step("gather data", state, T)
    assert mcp.calls == ["search_a"]


async def test_second_high_risk_call_is_suspended_for_approval_in_supervised_mode() -> None:
    hitl = HITLGateway()
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.REJECTED)  # type: ignore[method-assign]
    graph, state, mcp = _setup(
        ["search_a", "jira_update_issue"],
        extra_tools=(("jira_update_issue", "Jira"),),
        hitl_gateway=hitl,
        autonomy_mode="supervised",
    )

    with pytest.raises(PermissionError, match="rejected"):
        await graph._execute_step("gather data", state, T)
    hitl.wait_for_approval.assert_awaited()
    assert mcp.calls == ["search_a"]


async def test_second_high_risk_call_approved_in_supervised_mode_runs() -> None:
    hitl = HITLGateway()
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.APPROVED)  # type: ignore[method-assign]
    graph, state, mcp = _setup(
        ["search_a", "jira_update_issue"],
        extra_tools=(("jira_update_issue", "Jira"),),
        hitl_gateway=hitl,
        autonomy_mode="supervised",
    )

    await graph._execute_step("gather data", state, T)
    assert mcp.calls == ["search_a", "jira_update_issue"]


async def test_second_high_risk_call_is_refused_in_bounded_mode() -> None:
    graph, state, mcp = _setup(
        ["search_a", "jira_update_issue"], extra_tools=(("jira_update_issue", "Jira"),)
    )

    out = await graph._execute_step("gather data", state, T)
    assert mcp.calls == ["search_a"]
    assert "requires approval" in out


async def test_extra_calls_respect_the_tool_call_budget() -> None:
    graph, state, mcp = _setup(["search_a", "search_b", "search_c"])
    graph._tool_call_budget = 2

    out = await graph._execute_step("gather data", state, T)

    assert len(mcp.calls) == 2
    assert "budget" in out.lower()
