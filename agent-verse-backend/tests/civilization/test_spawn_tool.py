"""Tests for execute_spawn_tool — governed spawn tool exposed to agents."""
from unittest.mock import AsyncMock

import pytest

from app.civilization.models import SpawnDecision, SpawnVerdict
from app.civilization.spawn_tool import execute_spawn_tool


def _approved_verdict(**kwargs) -> SpawnVerdict:
    defaults = dict(
        decision=SpawnDecision.APPROVED,
        reason="spawn approved within Constitution bounds",
        allowed_budget_usd=5.0,
        clamped_autonomy="bounded-autonomous",
        inherited_policy_ids=[],
        snapshot={},
    )
    defaults.update(kwargs)
    return SpawnVerdict(**defaults)


def _denied_verdict(**kwargs) -> SpawnVerdict:
    defaults = dict(
        decision=SpawnDecision.DENIED,
        reason="depth 4 >= max_depth 4",
        allowed_budget_usd=0.0,
        snapshot={},
    )
    defaults.update(kwargs)
    return SpawnVerdict(**defaults)


def _make_tenant_ctx():
    from app.tenancy.context import PlanTier, TenantContext
    return TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")


@pytest.mark.asyncio
async def test_spawn_tool_returns_error_without_governor():
    result = await execute_spawn_tool(
        capability="jira",
        goal="search bugs",
        governor=None,
        requester_agent_id="a1",
        depth=1,
        parent_budget_usd=10.0,
        parent_policy_ids=[],
        tenant_ctx=_make_tenant_ctx(),
        civilization_id="civ-1",
    )
    assert result["success"] is False
    assert "Governor not available" in result["error"]


@pytest.mark.asyncio
async def test_spawn_tool_returns_denied_result_on_denial():
    mock_governor = AsyncMock()
    mock_governor.evaluate_spawn_request = AsyncMock(return_value=_denied_verdict())

    result = await execute_spawn_tool(
        capability="jira",
        goal="search bugs",
        governor=mock_governor,
        requester_agent_id="a1",
        depth=1,
        parent_budget_usd=10.0,
        parent_policy_ids=[],
        tenant_ctx=_make_tenant_ctx(),
        civilization_id="civ-1",
    )
    assert result["success"] is False
    assert result["denied"] is True
    assert "reason" in result
    assert "suggestion" in result


@pytest.mark.asyncio
async def test_spawn_tool_returns_success_on_approval():
    mock_governor = AsyncMock()
    mock_governor.evaluate_spawn_request = AsyncMock(return_value=_approved_verdict())
    mock_governor.spawn_agent = AsyncMock(return_value={
        "agent_id": "new-agent-123",
        "name": "JiraAgent",
    })

    result = await execute_spawn_tool(
        capability="jira",
        goal="search bugs",
        governor=mock_governor,
        requester_agent_id="a1",
        depth=1,
        parent_budget_usd=10.0,
        parent_policy_ids=[],
        tenant_ctx=_make_tenant_ctx(),
        civilization_id="civ-1",
    )
    assert result["success"] is True
    assert result["agent_id"] == "new-agent-123"
    assert result["capability"] == "jira"
    assert result["budget_usd"] == 5.0
    assert "JiraAgent" in result["message"]


@pytest.mark.asyncio
async def test_spawn_tool_submits_goal_when_goal_service_provided():
    mock_governor = AsyncMock()
    mock_governor.evaluate_spawn_request = AsyncMock(return_value=_approved_verdict())
    mock_governor.spawn_agent = AsyncMock(return_value={
        "agent_id": "new-agent-123",
        "name": "JiraAgent",
    })

    mock_goal_service = AsyncMock()
    mock_goal_service.submit_goal = AsyncMock(return_value={"goal_id": "goal-xyz"})

    result = await execute_spawn_tool(
        capability="jira",
        goal="search bugs",
        governor=mock_governor,
        requester_agent_id="a1",
        depth=1,
        parent_budget_usd=10.0,
        parent_policy_ids=[],
        tenant_ctx=_make_tenant_ctx(),
        goal_service=mock_goal_service,
        civilization_id="civ-1",
    )
    assert result["success"] is True
    assert result["goal_id"] == "goal-xyz"
    mock_goal_service.submit_goal.assert_called_once()


@pytest.mark.asyncio
async def test_spawn_tool_succeeds_even_if_goal_service_fails():
    mock_governor = AsyncMock()
    mock_governor.evaluate_spawn_request = AsyncMock(return_value=_approved_verdict())
    mock_governor.spawn_agent = AsyncMock(return_value={
        "agent_id": "new-agent-123",
        "name": "JiraAgent",
    })

    mock_goal_service = AsyncMock()
    mock_goal_service.submit_goal = AsyncMock(side_effect=RuntimeError("DB unavailable"))

    result = await execute_spawn_tool(
        capability="jira",
        goal="search bugs",
        governor=mock_governor,
        requester_agent_id="a1",
        depth=1,
        parent_budget_usd=10.0,
        parent_policy_ids=[],
        tenant_ctx=_make_tenant_ctx(),
        goal_service=mock_goal_service,
        civilization_id="civ-1",
    )
    # spawn still succeeds; goal_id is just None
    assert result["success"] is True
    assert result["goal_id"] is None


@pytest.mark.asyncio
async def test_spawn_tool_returns_error_on_spawn_exception():
    mock_governor = AsyncMock()
    mock_governor.evaluate_spawn_request = AsyncMock(return_value=_approved_verdict())
    mock_governor.spawn_agent = AsyncMock(side_effect=RuntimeError("AgentStore unavailable"))

    result = await execute_spawn_tool(
        capability="jira",
        goal="search bugs",
        governor=mock_governor,
        requester_agent_id="a1",
        depth=1,
        parent_budget_usd=10.0,
        parent_policy_ids=[],
        tenant_ctx=_make_tenant_ctx(),
        civilization_id="civ-1",
    )
    assert result["success"] is False
    assert "AgentStore unavailable" in result["error"]


@pytest.mark.asyncio
async def test_spawn_tool_denied_at_depth_limit_relays_reason_to_caller():
    """Depth-limit enforcement lives in the Constitution (app/civilization/constitution.py,
    covered by tests/civilization/test_constitution.py::test_spawn_denied_at_max_depth);
    this confirms the tool layer correctly surfaces that specific denial to the LLM
    rather than silently spawning or masking the reason."""
    mock_governor = AsyncMock()
    mock_governor.evaluate_spawn_request = AsyncMock(
        return_value=_denied_verdict(reason="depth 4 >= max_depth 4")
    )

    result = await execute_spawn_tool(
        capability="jira",
        goal="search bugs",
        governor=mock_governor,
        requester_agent_id="a1",
        depth=4,
        parent_budget_usd=10.0,
        parent_policy_ids=[],
        tenant_ctx=_make_tenant_ctx(),
        civilization_id="civ-1",
    )
    assert result["success"] is False
    assert result["denied"] is True
    assert "max_depth" in result["reason"]
    mock_governor.spawn_agent.assert_not_called()


@pytest.mark.asyncio
async def test_spawn_tool_evaluate_spawn_request_exception_fails_closed_with_error() -> None:
    """Regression: a governance-evaluation failure (e.g. metrics/DB hiccup in
    Governor.evaluate_spawn_request) must fail closed with a structured error
    like every other failure mode of this tool — not raise into the agent's
    tool-execution loop. Previously this call was unguarded (unlike spawn_agent's
    own try/except a few lines below it)."""
    mock_governor = AsyncMock()
    mock_governor.evaluate_spawn_request = AsyncMock(
        side_effect=RuntimeError("metrics store unreachable")
    )

    result = await execute_spawn_tool(
        capability="jira",
        goal="search bugs",
        governor=mock_governor,
        requester_agent_id="a1",
        depth=1,
        parent_budget_usd=10.0,
        parent_policy_ids=[],
        tenant_ctx=_make_tenant_ctx(),
        civilization_id="civ-1",
    )
    assert result["success"] is False
    assert "metrics store unreachable" in result["error"]
    mock_governor.spawn_agent.assert_not_called()


@pytest.mark.asyncio
async def test_spawn_tool_does_not_submit_goal_when_spawn_agent_fails() -> None:
    """Partial-state containment: if agent creation itself fails, no goal must ever
    be submitted for the (nonexistent) agent — the failure must short-circuit cleanly
    rather than leaving an orphaned goal pointed at an agent_id that was never created."""
    mock_governor = AsyncMock()
    mock_governor.evaluate_spawn_request = AsyncMock(return_value=_approved_verdict())
    mock_governor.spawn_agent = AsyncMock(side_effect=RuntimeError("AgentStore unavailable"))

    mock_goal_service = AsyncMock()
    mock_goal_service.submit_goal = AsyncMock(return_value={"goal_id": "should-not-happen"})

    result = await execute_spawn_tool(
        capability="jira",
        goal="search bugs",
        governor=mock_governor,
        requester_agent_id="a1",
        depth=1,
        parent_budget_usd=10.0,
        parent_policy_ids=[],
        tenant_ctx=_make_tenant_ctx(),
        goal_service=mock_goal_service,
        civilization_id="civ-1",
    )
    assert result["success"] is False
    mock_goal_service.submit_goal.assert_not_called()


@pytest.mark.asyncio
async def test_spawn_tool_handles_missing_agent_id_in_spawn_result() -> None:
    """Malformed/partial data from governor.spawn_agent (missing 'agent_id') must
    degrade to a placeholder rather than raising a KeyError."""
    mock_governor = AsyncMock()
    mock_governor.evaluate_spawn_request = AsyncMock(return_value=_approved_verdict())
    mock_governor.spawn_agent = AsyncMock(return_value={"name": "NamelessAgent"})  # no agent_id

    result = await execute_spawn_tool(
        capability="jira",
        goal="search bugs",
        governor=mock_governor,
        requester_agent_id="a1",
        depth=1,
        parent_budget_usd=10.0,
        parent_policy_ids=[],
        tenant_ctx=_make_tenant_ctx(),
        civilization_id="civ-1",
    )
    assert result["success"] is True
    assert result["agent_id"] == "unknown"


@pytest.mark.asyncio
async def test_spawn_tool_repeated_calls_respect_governor_denials_after_limit_reached() -> None:
    """Runaway-spawning prevention (rate/total-agent limits, enforced by the
    Constitution) must be re-evaluated on every call — a burst of spawn attempts
    stops creating agents the moment the Governor starts denying, it does not
    keep spawning based on a stale/cached verdict."""
    verdicts = [_approved_verdict(), _approved_verdict(), _denied_verdict(reason="spawn_rate 5/min >= limit 5/min")]
    mock_governor = AsyncMock()
    mock_governor.evaluate_spawn_request = AsyncMock(side_effect=verdicts)
    mock_governor.spawn_agent = AsyncMock(
        side_effect=[
            {"agent_id": "agent-1", "name": "A1"},
            {"agent_id": "agent-2", "name": "A2"},
        ]
    )

    results = [
        await execute_spawn_tool(
            capability="jira",
            goal=f"task {i}",
            governor=mock_governor,
            requester_agent_id="a1",
            depth=1,
            parent_budget_usd=10.0,
            parent_policy_ids=[],
            tenant_ctx=_make_tenant_ctx(),
            civilization_id="civ-1",
        )
        for i in range(3)
    ]

    assert [r["success"] for r in results] == [True, True, False]
    assert results[2]["denied"] is True
    assert mock_governor.spawn_agent.call_count == 2
