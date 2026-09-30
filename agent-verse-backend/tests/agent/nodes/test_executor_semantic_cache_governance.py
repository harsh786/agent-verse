"""A semantic-cache hit must never let a step skip governance.

The executor used to look the step up in the semantic cache BEFORE the governed
pipeline ran, so a warm entry returned an answer for a step that the approval
gate (HITL / action-safety / policy REQUIRE_APPROVAL), the permission matrix,
the tool policy engine, grants or per-agent permissions would have refused.
The batch prefetch in ``_node_execute`` had the same bypass.

These tests seed the cache the way production does — by running a benign step
through the executor (the fake embedder maps every single text to the same
vector, so the entry matches any later step) — and then show that governance
still decides the later step.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.agent.tool_context import ToolContext, ToolRef
from app.governance.grants.models import Grant
from app.governance.grants.store import InMemoryGrantStore
from app.governance.hitl import ApprovalStatus, HITLGateway
from app.governance.policies import PolicyResult
from app.providers.fake import FakeProvider
from app.rag.semantic_cache import SemanticCache
from app.tenancy.context import PlanTier, TenantContext

T_A = TenantContext(tenant_id="cache-gov-tenant-a", plan=PlanTier.ENTERPRISE, api_key_id="ka")
T_B = TenantContext(tenant_id="cache-gov-tenant-b", plan=PlanTier.ENTERPRISE, api_key_id="kb")

CACHED = "The quarterly report shows revenue grew twelve percent year over year."
FRESH = "A freshly computed answer that did not come from the cache at all."


class _RecordingMCPClient:
    def __init__(self, output: object) -> None:
        self.calls: list[dict[str, object]] = []
        self._output = output

    async def call_tool(self, **kwargs: object) -> object:
        self.calls.append(kwargs)

        class _Result:
            success = True
            output = self._output
            error = ""

        return _Result()


def _graph(executor_responses: list[str], **kwargs: Any) -> AgentGraph:
    kwargs.setdefault("semantic_cache", SemanticCache())
    kwargs.setdefault("embedder", FakeProvider())
    return AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=FakeProvider(responses=executor_responses),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        **kwargs,
    )


def _state(step: str, tenant: TenantContext = T_A) -> AgentState:
    state = AgentState(goal="goal", tenant_ctx=tenant)
    state.steps.append(StepResult(description=step, status=StepStatus.RUNNING))
    return state


def _events(graph: AgentGraph) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    async def _collect(event: dict[str, Any]) -> None:
        events.append(event)

    graph._event_callback = _collect  # type: ignore[assignment]
    return events


async def _warm(graph: AgentGraph, tenant: TenantContext = T_A) -> None:
    """Populate the cache through the real executor path (a benign step)."""
    step = "summarize the quarterly report"
    out = await graph._execute_step_with_cache(step, _state(step, tenant), tenant)
    assert out == CACHED


async def test_high_risk_step_with_warm_cache_is_refused_in_bounded_mode() -> None:
    graph = _graph([CACHED, FRESH])
    await _warm(graph)
    events = _events(graph)

    step = "deploy the service to prod"
    with pytest.raises(PermissionError, match="requires human approval"):
        await graph._execute_step_with_cache(step, _state(step), T_A)
    assert not any(e.get("type") == "cache_hit" for e in events)


async def test_high_risk_step_with_warm_cache_suspends_for_approval_in_supervised_mode() -> None:
    hitl = HITLGateway()
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.REJECTED)  # type: ignore[method-assign]
    graph = _graph([CACHED, FRESH], hitl_gateway=hitl, autonomy_mode="supervised")
    await _warm(graph)
    events = _events(graph)

    step = "deploy the service to prod"
    with pytest.raises(PermissionError, match="rejected"):
        await graph._execute_step_with_cache(step, _state(step), T_A)
    hitl.wait_for_approval.assert_awaited()  # the step waited for a human
    assert any(e.get("type") == "waiting_approval" for e in events)
    assert not any(e.get("type") == "cache_hit" for e in events)


async def test_approved_high_risk_step_is_executed_not_served_or_stored() -> None:
    """Even an approved side-effecting step runs for real and is never cached."""
    hitl = HITLGateway()
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.APPROVED)  # type: ignore[method-assign]
    cache = SemanticCache()
    graph = _graph([CACHED, FRESH], hitl_gateway=hitl, autonomy_mode="supervised",
                   semantic_cache=cache)
    await _warm(graph)

    step = "deploy the service to prod"
    out = await graph._execute_step_with_cache(step, _state(step), T_A)
    assert out == FRESH
    # The approved deploy's output did not become the cached answer.
    probe = _graph([FRESH], semantic_cache=cache)
    served = await probe._execute_step_with_cache(
        "summarize the quarterly report", _state("summarize the quarterly report"), T_A
    )
    assert served == CACHED


async def test_policy_require_approval_with_warm_cache_is_refused() -> None:
    policy = MagicMock()
    policy.evaluate.return_value = PolicyResult.ALLOW
    graph = _graph([CACHED, FRESH], policy_engine=policy)
    await _warm(graph)

    policy.evaluate.return_value = PolicyResult.REQUIRE_APPROVAL
    step = "call salesforce to fetch the account list"
    with pytest.raises(PermissionError, match="requires human approval"):
        await graph._execute_step_with_cache(step, _state(step), T_A)


async def test_policy_denied_tool_never_serves_from_cache() -> None:
    policy = MagicMock()
    policy.evaluate.return_value = PolicyResult.ALLOW
    graph = _graph([CACHED, FRESH], policy_engine=policy)
    await _warm(graph)

    policy.evaluate.return_value = PolicyResult.DENY
    step = "call salesforce to fetch the account list"
    with pytest.raises(PermissionError, match="denied by governance policy"):
        await graph._execute_step_with_cache(step, _state(step), T_A)


async def test_revoked_tool_grant_is_not_bypassed_by_a_cached_tool_result() -> None:
    """A step whose cached answer came from a tool the agent may no longer use
    must re-run through the real tool gate instead of serving the old result."""
    tc = ToolContext(
        connectors=[],
        tools=[
            ToolRef(server_id="s1", server_name="Custom", name="get_status",
                    description="status", input_schema={})
        ],
    )
    store = InMemoryGrantStore()
    now = datetime.now(UTC)
    await store.issue(
        Grant(grant_id="g1", tenant_id=T_A.tenant_id, grantor="owner", grantee_agent_id="",
              scopes=("*",), not_before=now - timedelta(minutes=1),
              expires_at=now + timedelta(hours=1))
    )
    mcp = _RecordingMCPClient(output={"status": "all systems nominal and green"})
    tool_json = '{"tool": "get_status", "arguments": {}}'
    graph = _graph([tool_json, tool_json], mcp_client=mcp, grant_store=store,
                   enforce_grants=True)

    step = "check status"
    first_state = _state(step)
    first_state.context["tool_context"] = tc
    first = await graph._execute_step_with_cache(step, first_state, T_A)
    assert "nominal" in first
    assert len(mcp.calls) == 1

    await store.revoke(T_A.tenant_id, "g1")
    events = _events(graph)
    second_state = _state(step)  # a different goal: no in-goal dedup
    second_state.context["tool_context"] = tc
    second = await graph._execute_step_with_cache(step, second_state, T_A)

    assert not any(e.get("type") == "cache_hit" for e in events)
    assert len(mcp.calls) == 1  # the revoked tool did not run again
    assert "nominal" not in second  # and its old result was not served


async def test_refused_tool_call_never_populates_the_cache() -> None:
    tc = ToolContext(
        connectors=[],
        tools=[
            ToolRef(server_id="s1", server_name="Custom", name="get_status",
                    description="status", input_schema={})
        ],
    )
    cache = SemanticCache()
    mcp = _RecordingMCPClient(output={"status": "ok"})
    graph = _graph(['{"tool": "get_status", "arguments": {}}'], mcp_client=mcp,
                   grant_store=InMemoryGrantStore(), enforce_grants=True, semantic_cache=cache)
    step = "check status"
    state = _state(step)
    state.context["tool_context"] = tc
    refused = await graph._execute_step_with_cache(step, state, T_A)
    assert mcp.calls == []
    assert "denied" in refused.lower()

    probe = _graph([FRESH], semantic_cache=cache)
    assert await probe._execute_step_with_cache(step, _state(step), T_A) == FRESH


def test_refusal_outputs_are_recognised_as_uncacheable() -> None:
    from app.agent.nodes import executor_mixin

    blocked = "Action blocked by safety profile: tool is blocked for this tenant"
    assert executor_mixin._is_step_refusal(blocked)
    assert executor_mixin._is_step_refusal("Tool call denied: 'x' is not permitted for this agent")
    assert executor_mixin._is_step_refusal("Guardrail blocked step: injection phrase")
    assert not executor_mixin._is_step_refusal(CACHED)


async def test_cache_entries_are_tenant_isolated() -> None:
    cache = SemanticCache()
    graph = _graph([CACHED, FRESH], semantic_cache=cache)
    await _warm(graph, T_A)

    step = "summarize the quarterly report"
    out_b = await graph._execute_step_with_cache(step, _state(step, T_B), T_B)
    assert out_b == FRESH


async def test_cache_entries_are_agent_scoped() -> None:
    """Agent permissions/grants are per agent, so one agent's cached tool result
    is never served to another agent of the same tenant."""
    cache = SemanticCache()
    first = _graph([CACHED], semantic_cache=cache)
    first._agent_id = "agent-one"
    await _warm(first)

    second = _graph([FRESH], semantic_cache=cache)
    second._agent_id = "agent-two"
    step = "summarize the quarterly report"
    assert await second._execute_step_with_cache(step, _state(step), T_A) == FRESH


async def test_warm_cache_still_serves_a_governed_benign_step() -> None:
    """The fix must not disable caching: a benign step is still served."""
    graph = _graph([CACHED, FRESH])
    await _warm(graph)
    events = _events(graph)

    step = "summarize the quarterly report"
    assert await graph._execute_step_with_cache(step, _state(step), T_A) == CACHED
    assert any(e.get("type") == "cache_hit" for e in events)


async def test_batch_prefetch_does_not_bypass_the_approval_gate() -> None:
    """The run-level batch prefetch used to serve a hit before any gate ran."""
    cache = SemanticCache()
    embedder = FakeProvider()
    seed = _graph([CACHED], semantic_cache=cache, embedder=embedder)
    await _warm(seed)

    p = FakeProvider(
        responses=[
            '{"steps": ["deploy the service to prod"]}',
            '{"success": false, "reason": "not done"}',
        ]
    )
    g = AgentGraph(planner=p, executor=p, verifier=p, semantic_cache=cache, embedder=embedder,
                   max_iterations=1)
    events: list[dict[str, Any]] = []

    async def cb(e: dict[str, Any]) -> None:
        events.append(e)

    with pytest.raises(PermissionError):
        await g.run(goal="deploy the service to prod", tenant_ctx=T_A, event_callback=cb)
    assert not any(e.get("type") == "cache_hit" for e in events)
