"""Orchestration state must persist after goal execution."""
from __future__ import annotations

import pytest

from app.evals.runtime_scorecard import RuntimeScorecard, ScorecardResult
from app.agent.state import AgentState, GoalStatus
from app.orchestration.runtime_profile import (
    GoalRuntimeProfile,
    GoalProperties,
    AgentPatternConfig,
    RAGStrategyConfig,
    ModelPlanConfig,
    SecurityConfig,
    MemoryCacheConfig,
    EvalConfig,
)
from app.tenancy.context import TenantContext, PlanTier
from app.services.orchestration_persistence import OrchestrationPersistence


@pytest.fixture
def tenant_ctx() -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def profile() -> GoalRuntimeProfile:
    return GoalRuntimeProfile(
        goal_id="g1",
        tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )


def test_orchestration_persistence_exists() -> None:
    persistence = OrchestrationPersistence()
    assert persistence is not None


async def test_persist_scorecard_no_db(
    tenant_ctx: TenantContext, profile: GoalRuntimeProfile
) -> None:
    """persist_scorecard must not crash without DB."""
    persistence = OrchestrationPersistence(db=None)
    state = AgentState(goal="list tickets", tenant_ctx=tenant_ctx, goal_id="g1")
    state.status = GoalStatus.COMPLETE
    state.iterations = 3
    scorecard_result = ScorecardResult(
        goal_id="g1",
        scores={
            "goal_success": 1.0,
            "rag_quality": 0.8,
            "safety": 1.0,
            "latency": 0.9,
            "cost_efficiency": 0.9,
            "grounding": 0.95,
            "citation_quality": 0.8,
            "retrieval_confidence": 0.7,
            "tool_success_rate": 1.0,
        },
        overall_score=0.91,
    )
    await persistence.persist_scorecard(scorecard_result, profile=profile, db=None)


async def test_persist_reflexion_lesson_no_db(tenant_ctx: TenantContext) -> None:
    """persist_reflexion_lesson must store in memory when no DB."""
    from app.state_runtime.reflexion_store import ReflexionStore

    persistence = OrchestrationPersistence(reflexion_store=ReflexionStore())
    state = AgentState(
        goal="delete prod db", tenant_ctx=tenant_ctx, goal_id="g1"
    )
    state.status = GoalStatus.FAILED
    state.verification_feedback = "permission denied"
    await persistence.persist_reflexion_lesson(state, db=None)
    lessons = persistence._reflexion_store.recall(tenant_id="t1", limit=5)
    assert len(lessons) >= 1


async def test_persist_tool_trust_no_db() -> None:
    """persist_tool_trust must store outcomes in memory when no DB."""
    from app.tool_runtime.tool_trust_store import ToolTrustStore

    store = ToolTrustStore()
    persistence = OrchestrationPersistence(tool_trust_store=store)
    await persistence.persist_tool_outcome(
        tool_name="jira.search_issues",
        success=True,
        latency_ms=300,
        tenant_id="t1",
        db=None,
    )
    history = store.get_history("jira.search_issues")
    assert len(history) >= 1
    assert history[0]["success"] is True


def test_after_goal_complete_state_contains_scorecard() -> None:
    ctx = TenantContext(
        tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1"
    )
    state = AgentState(goal="test", tenant_ctx=ctx, goal_id="g1")
    state.status = GoalStatus.COMPLETE
    state.iterations = 3
    p = GoalRuntimeProfile(
        goal_id="g1",
        tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )
    scorecard = RuntimeScorecard()
    result = scorecard.score(state=state, profile=p)
    state.context["scorecard"] = result.to_dict()
    assert "scorecard" in state.context
    assert state.context["scorecard"]["overall_score"] >= 0.0
