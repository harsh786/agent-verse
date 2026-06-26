"""Tests for AgentRouter — intent-based agent routing."""
from __future__ import annotations

import pytest

from app.agent.router import AgentRouter, RoutingDecision
from app.api.agents import AgentStore
from app.tenancy.context import PlanTier, TenantContext

# ── shared fixtures ──────────────────────────────────────────────────────────

_CTX_A = TenantContext(tenant_id="tenant-a", plan=PlanTier.PROFESSIONAL, api_key_id="k-a")
_CTX_B = TenantContext(tenant_id="tenant-b", plan=PlanTier.FREE, api_key_id="k-b")


def _make_store() -> AgentStore:
    return AgentStore()


async def _add_agent(
    store: AgentStore,
    *,
    name: str,
    goal_template: str = "",
    connector_ids: list[str] | None = None,
    tenant_ctx: TenantContext = _CTX_A,
) -> str:
    return await store.create(
        {
            "name": name,
            "goal_template": goal_template,
            "connector_ids": connector_ids or [],
            "autonomy_mode": "bounded-autonomous",
            "trigger_config": {},
        },
        tenant_ctx=tenant_ctx,
    )


# ── tests ────────────────────────────────────────────────────────────────────


async def test_route_selects_by_connector_match() -> None:
    """Agent whose connector ID appears in the goal should be selected."""
    store = _make_store()
    aid = await _add_agent(
        store,
        name="github automation",
        goal_template="manage repositories",
        connector_ids=["github"],
    )
    router = AgentRouter(agent_store=store)
    decision = await router.route("open a pull request in github", _CTX_A)
    assert decision.agent_id == aid
    assert decision.confidence >= 0.3


async def test_route_selects_by_name_keyword() -> None:
    """Agent whose name appears in the goal should score higher than an unrelated agent."""
    store = _make_store()
    # github agent — name AND connector match the goal
    gh_id = await _add_agent(
        store,
        name="github automation",
        goal_template="automate github workflows",
        connector_ids=["github"],
    )
    # slack agent — no overlap with a github goal
    await _add_agent(
        store,
        name="slack notifier",
        goal_template="send slack messages",
        connector_ids=["slack"],
    )
    router = AgentRouter(agent_store=store)
    decision = await router.route("create a github issue", _CTX_A)
    assert decision.agent_id == gh_id


async def test_route_returns_none_when_no_agents() -> None:
    """Router must return agent_id=None when no agents are registered."""
    store = _make_store()
    router = AgentRouter(agent_store=store)
    decision = await router.route("create a github issue", _CTX_A)
    assert decision.agent_id is None
    assert decision.reason == "no_agents"


async def test_route_returns_none_when_confidence_too_low() -> None:
    """Router must return agent_id=None when the best composite score < 0.3."""
    store = _make_store()
    # Agent with connector 'xenosystem' — completely unrelated to the goal
    await _add_agent(store, name="xenosystem agent", connector_ids=["xenosystem"])
    router = AgentRouter(agent_store=store)
    decision = await router.route("translate text from english to french", _CTX_A)
    assert decision.agent_id is None
    assert decision.confidence < 0.3


async def test_route_all_scores_contains_every_agent() -> None:
    """all_scores must contain exactly one entry per registered agent."""
    store = _make_store()
    for i in range(3):
        await _add_agent(store, name=f"agent-{i}", connector_ids=[f"svc{i}"])
    router = AgentRouter(agent_store=store)
    decision = await router.route("do something unrelated", _CTX_A)
    assert len(decision.all_scores) == 3


async def test_route_tenant_isolation() -> None:
    """Agents registered under tenant-b must not appear when routing for tenant-a."""
    store = _make_store()
    # Register a high-scoring agent under tenant-b
    await _add_agent(
        store,
        name="github automation",
        goal_template="manage github repos and issues",
        connector_ids=["github"],
        tenant_ctx=_CTX_B,
    )
    router = AgentRouter(agent_store=store)
    # Routing for tenant-a should find no agents at all
    decision = await router.route("create a github issue", _CTX_A)
    assert decision.agent_id is None
    assert decision.reason == "no_agents"


async def test_route_by_goal_template_keyword_overlap() -> None:
    """Agent whose goal_template overlaps with the goal text should be selected."""
    store = _make_store()
    aid = await _add_agent(
        store,
        name="salesforce agent",
        goal_template="manage leads and contacts in salesforce crm",
        connector_ids=["salesforce"],
    )
    router = AgentRouter(agent_store=store)
    decision = await router.route("analyze salesforce leads and contacts", _CTX_A)
    assert decision.agent_id == aid
    assert decision.confidence >= 0.3


async def test_route_selects_highest_scoring_agent() -> None:
    """When multiple agents are registered, the highest-scoring one wins."""
    store = _make_store()
    gh_id = await _add_agent(
        store,
        name="github agent",
        goal_template="manage github repos and issues",
        connector_ids=["github"],
    )
    # Slack agent — no overlap with a github goal
    await _add_agent(
        store,
        name="slack agent",
        goal_template="send messages in slack channels",
        connector_ids=["slack"],
    )
    router = AgentRouter(agent_store=store)
    decision = await router.route("create a github issue", _CTX_A)
    assert decision.agent_id == gh_id


async def test_route_history_fallback_returns_zero() -> None:
    """_score_by_history must return 0.0 when no eval_store is configured."""
    store = _make_store()
    aid = await _add_agent(store, name="agent-x", connector_ids=["x"])
    router = AgentRouter(agent_store=store, eval_store=None)
    score = router._score_by_history(aid, _CTX_A)
    assert score == 0.0
