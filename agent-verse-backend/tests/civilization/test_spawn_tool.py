"""Tests for execute_spawn_tool — governed spawn tool exposed to agents."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.civilization.spawn_tool import execute_spawn_tool
from app.civilization.models import SpawnVerdict, SpawnDecision


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
    from app.tenancy.context import TenantContext, PlanTier
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
