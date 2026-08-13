from __future__ import annotations

import pytest

from app.routing_runtime.contracts import RoutingCandidate
from app.routing_runtime.decision_store import InMemoryDecisionStore
from app.routing_runtime.embedding_router import EmbeddingRouter
from app.routing_runtime.model_router import ModelRouter
from app.routing_runtime.skill_router import SkillCandidate, SkillRouter
from app.routing_runtime.tool_router import ToolRouter
from tests.routing_runtime.test_contracts import candidate, signals


@pytest.mark.asyncio
async def test_model_router_enforces_hard_constraints_and_stable_fallback() -> None:
    fast = candidate("fast")
    expensive = candidate("expensive").model_copy(update={"estimated_cost_usd": 2})
    slow = candidate("slow").model_copy(update={"estimated_latency_ms": 2_000})
    unavailable = candidate("down").model_copy(update={"readiness": "circuit_open"})
    decision = await ModelRouter(decision_store=InMemoryDecisionStore()).route(
        tenant_id="tenant",
        goal_id="goal",
        execution_id="execution",
        ordinal=1,
        signals=signals(),
        candidates=(slow, unavailable, expensive, fast),
    )
    assert decision.selected_candidate_id == "fast"
    assert decision.fallback_chain == ("fast",)
    assert {item.candidate_id: item.rejection_reasons for item in decision.candidates}["down"] == (
        "circuit_open",
    )


@pytest.mark.asyncio
async def test_skill_router_intersects_tenant_version_and_token_budget() -> None:
    valid = SkillCandidate(
        routing=candidate("valid"),
        semantic_score=9500,
        allowed_tenant_ids=frozenset({"tenant"}),
        compatible_versions=frozenset({"v1"}),
        token_cost=10,
    )
    denied = valid.model_copy(
        update={"routing": candidate("denied"), "allowed_tenant_ids": frozenset({"other"})}
    )
    decision = await SkillRouter(decision_store=InMemoryDecisionStore()).route(
        tenant_id="tenant",
        goal_id="goal",
        execution_id="execution",
        ordinal=1,
        signals=signals(),
        candidates=(denied, valid),
        required_version="v1",
    )
    assert decision.selected_candidate_id == "valid"


@pytest.mark.asyncio
async def test_tool_router_reauthorizes_at_selection_and_dispatch() -> None:
    allowed = True

    async def authorize(_tenant: str, _tool: str) -> bool:
        return allowed

    router = ToolRouter(decision_store=InMemoryDecisionStore(), authorizer=authorize)
    decision = await router.route(
        tenant_id="tenant",
        goal_id="goal",
        execution_id="execution",
        ordinal=1,
        signals=signals(),
        candidates=(candidate("tool"),),
    )
    assert decision.selected_candidate_id == "tool"
    allowed = False
    with pytest.raises(PermissionError):
        await router.reauthorize_before_dispatch("tenant", "tool")


@pytest.mark.asyncio
async def test_embedding_router_degrades_explicitly_to_lexical() -> None:
    unavailable: RoutingCandidate = candidate("embed").model_copy(
        update={"readiness": "unavailable"}
    )
    decision = await EmbeddingRouter(decision_store=InMemoryDecisionStore()).route(
        tenant_id="tenant",
        goal_id="goal",
        execution_id="execution",
        ordinal=1,
        signals=signals(),
        candidates=(unavailable,),
        allow_lexical_fallback=True,
    )
    assert decision.selected_candidate_id == "lexical"
    assert decision.policy_trace == {"degraded_to": "lexical"}
