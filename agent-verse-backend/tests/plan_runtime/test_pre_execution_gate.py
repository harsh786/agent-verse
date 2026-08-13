from app.orchestration.runtime_profile import (
    AgentPatternConfig,
    EvalConfig,
    GoalProperties,
    GoalRuntimeProfile,
    MemoryCacheConfig,
    ModelPlanConfig,
    RAGStrategyConfig,
    SecurityConfig,
)
from app.plan_runtime.plan_verifier import PlanVerifier


def test_pre_execution_gate_serializes_findings_and_denies_before_execution() -> None:
    profile = GoalRuntimeProfile(
        goal_id="g",
        tenant_id="t",
        properties=GoalProperties(raw_goal="delete prod"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(sandbox_required=True, hitl_required=True),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )
    result = PlanVerifier().verify_pre_execution(
        plan=["delete production data"],
        profile=profile,
        required_permissions=frozenset({"delete"}),
        granted_permissions=frozenset(),
        required_context=frozenset({"backup"}),
        available_context=frozenset(),
        data_classes=frozenset({"restricted"}),
        sandbox_ready=False,
        hitl_approved=False,
    )
    assert not result.safe_to_execute
    serialized = result.to_dict()
    assert serialized["missing_permissions"] == ["delete"]
    assert serialized["missing_context"] == ["backup"]
