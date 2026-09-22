"""Cost breakdown API endpoint — per-role token/cost attribution per goal,
plus optional LLM response-cache stats merged in when a tenant context and
cache are present on the request/app state.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.observability.cost_breakdown import get_breakdown
from app.observability.cost_breakdown_api import get_goal_cost_metrics


def _request(*, tenant=None, llm_cache=None):
    request = MagicMock()
    request.state = SimpleNamespace(tenant=tenant)
    request.app.state = SimpleNamespace(llm_response_cache=llm_cache)
    return request


@pytest.mark.asyncio
async def test_returns_breakdown_dict_for_unknown_goal():
    """An unrecorded goal_id still returns a well-formed (empty) breakdown."""
    result = await get_goal_cost_metrics("goal-unknown-1", _request())
    assert result["goal_id"] == "goal-unknown-1"
    assert result["total_cost_usd"] == 0
    assert result["roles"] == []
    assert "llm_cache" not in result


@pytest.mark.asyncio
async def test_returns_recorded_role_entries():
    bd = get_breakdown("goal-recorded")
    bd.record("planner", "claude-sonnet", 100, 50, 0.01)
    bd.record("executor", "claude-haiku", 200, 20, 0.002)

    result = await get_goal_cost_metrics("goal-recorded", _request())

    assert result["goal_id"] == "goal-recorded"
    roles = {r["role"]: r for r in result["roles"]}
    assert roles["planner"]["model"] == "claude-sonnet"
    assert roles["planner"]["input_tokens"] == 100
    assert roles["executor"]["input_tokens"] == 200
    assert result["total_cost_usd"] == pytest.approx(0.012)


@pytest.mark.asyncio
async def test_no_tenant_skips_cache_stats():
    """Without a tenant on request.state, llm_cache is never consulted even if present."""
    llm_cache = MagicMock()
    request = _request(tenant=None, llm_cache=llm_cache)
    result = await get_goal_cost_metrics("goal-x", request)
    assert "llm_cache" not in result
    llm_cache.stats.assert_not_called()


@pytest.mark.asyncio
async def test_no_llm_cache_on_app_state_skips_cache_stats():
    tenant = SimpleNamespace(tenant_id="t1")
    request = _request(tenant=tenant, llm_cache=None)
    result = await get_goal_cost_metrics("goal-y", request)
    assert "llm_cache" not in result


@pytest.mark.asyncio
async def test_tenant_and_cache_present_merges_cache_stats():
    tenant = SimpleNamespace(tenant_id="t1")
    llm_cache = MagicMock()
    llm_cache.stats.return_value = {"hits": 5, "misses": 2}
    request = _request(tenant=tenant, llm_cache=llm_cache)

    result = await get_goal_cost_metrics("goal-z", request)

    llm_cache.stats.assert_called_once_with("t1")
    assert result["llm_cache"] == {"hits": 5, "misses": 2}


@pytest.mark.asyncio
async def test_cache_stats_failure_is_swallowed():
    """A broken cache backend must not break the cost-metrics endpoint."""
    tenant = SimpleNamespace(tenant_id="t1")
    llm_cache = MagicMock()
    llm_cache.stats.side_effect = RuntimeError("redis down")
    request = _request(tenant=tenant, llm_cache=llm_cache)

    result = await get_goal_cost_metrics("goal-broken-cache", request)

    assert "llm_cache" not in result
    assert result["goal_id"] == "goal-broken-cache"
