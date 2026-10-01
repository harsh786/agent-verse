"""CORE-33: auto-routing pre-filters candidates by relevance, bounded.

POST /goals read the tenant's whole agents table per submission and the router
then truncated to the 50 NEWEST, so large tenants paid a full load and older
agents were never candidates. The router now asks the store for at most
MAX_ROUTING_CANDIDATES agents ranked by text match against the goal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.agent.router import MAX_ROUTING_CANDIDATES, AgentRouter
from app.api.agents import AgentStore
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-route-cand", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _store_with_200_agents() -> AgentStore:
    store = AgentStore()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # The OLDEST agent is the only real match for the goal.
    store._data[(CTX.tenant_id, "best")] = {
        "agent_id": "best", "tenant_id": CTX.tenant_id, "name": "Invoice reconciler",
        "goal_template": "reconcile vendor invoices against payments",
        "connector_ids": ["builtin-stripe"], "created_at": base.isoformat(),
    }
    for i in range(199):
        aid = f"filler{i:03d}"
        store._data[(CTX.tenant_id, aid)] = {
            "agent_id": aid, "tenant_id": CTX.tenant_id, "name": f"Helper {i}",
            "goal_template": "general assistance", "connector_ids": [],
            "created_at": (base + timedelta(days=i + 1)).isoformat(),
        }
    return store


@pytest.mark.asyncio
async def test_oldest_best_match_is_routed_among_200_agents() -> None:
    router = AgentRouter(agent_store=_store_with_200_agents())
    decision = await router.route("reconcile the vendor invoices with stripe payments", CTX)
    assert decision.agent_id == "best"
    assert len(decision.all_scores) <= MAX_ROUTING_CANDIDATES


@pytest.mark.asyncio
async def test_store_candidates_are_bounded_and_relevance_ranked() -> None:
    store = _store_with_200_agents()
    out = await store.routing_candidates(
        tenant_ctx=CTX, goal="reconcile vendor invoices", limit=MAX_ROUTING_CANDIDATES
    )
    assert len(out) == MAX_ROUTING_CANDIDATES
    assert out[0]["agent_id"] == "best"


@pytest.mark.asyncio
async def test_router_never_loads_the_whole_table() -> None:
    calls: list[dict[str, Any]] = []

    class _Store:
        async def routing_candidates(self, **kwargs: Any) -> list[dict[str, Any]]:
            calls.append(kwargs)
            return []

        async def list_async(self, **kwargs: Any) -> list[dict[str, Any]]:
            raise AssertionError("routing must not list the agents table")

    await AgentRouter(agent_store=_Store()).route("anything", CTX)
    assert calls and calls[0]["limit"] == MAX_ROUTING_CANDIDATES


def test_goals_api_lets_the_router_fetch_its_own_candidates() -> None:
    import inspect

    import app.api.goals as goals_mod

    src = inspect.getsource(goals_mod)
    assert "agent_store.list_async(tenant_ctx=tenant)" not in src
