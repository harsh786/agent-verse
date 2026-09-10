from __future__ import annotations

import json

from app.explainability_runtime.decision_explainer import DecisionExplainer, ExplanationBundle
from app.explainability_runtime.runtime_profile_explainer import RuntimeProfileExplainer
from app.explainability_runtime.source_explainer import SourceExplainer
from app.orchestration.decision_trace import DecisionTrace
from app.orchestration.runtime_profile import (
    AgentPatternConfig,
    EvalConfig,
    GoalProperties,
    GoalRuntimeProfile,
    MemoryCacheConfig,
    ModelPlanConfig,
    RAGStrategyConfig,
    RiskLevel,
    SecurityConfig,
)


def _make_profile(risk=RiskLevel.LOW):
    return GoalRuntimeProfile(
        goal_id="g1",
        tenant_id="t1",
        properties=GoalProperties(raw_goal="delete prod db", risk=risk),
        agent_patterns=AgentPatternConfig(
            safety=["guardrails", "hitl", "rollback"],
            selection_reasons={"hitl": "risk=critical"},
        ),
        rag_strategy=RAGStrategyConfig(strategy="hybrid_rag"),
        model_plan=ModelPlanConfig(planner="gpt-5.2"),
        security=SecurityConfig(
            hitl_required=(risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)),
            audit_level="forensic",
        ),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )


def test_decision_explainer_explains_model():
    explainer = DecisionExplainer()
    profile = _make_profile(RiskLevel.CRITICAL)
    trace = DecisionTrace(goal_id="g1", tenant_id="t1")
    trace.add("PatternSelector", "model_plan", "high", "risk=critical — high quality required")
    bundle = explainer.explain(profile, trace)
    assert isinstance(bundle, ExplanationBundle)
    assert bundle.why_this_model is not None and len(bundle.why_this_model) > 0


def test_decision_explainer_explains_rag():
    explainer = DecisionExplainer()
    profile = _make_profile()
    trace = DecisionTrace(goal_id="g1", tenant_id="t1")
    trace.add("PatternSelector", "rag_strategy", "hybrid_rag", "kb available")
    bundle = explainer.explain(profile, trace)
    assert bundle.why_this_rag is not None


def test_decision_explainer_explains_guardrail():
    explainer = DecisionExplainer()
    profile = _make_profile(RiskLevel.CRITICAL)
    trace = DecisionTrace(goal_id="g1", tenant_id="t1")
    bundle = explainer.explain(profile, trace)
    assert "hitl" in bundle.why_this_guardrail.lower() or "risk" in bundle.why_this_guardrail.lower()


def test_runtime_profile_explainer_produces_summary():
    explainer = RuntimeProfileExplainer()
    profile = _make_profile()
    summary = explainer.summarize(profile)
    assert isinstance(summary, str) and len(summary) > 50


def test_source_explainer_explains_fallback():
    explainer = SourceExplainer()
    explanation = explainer.explain_fallback(
        "knowledge_base", "web", "confidence=0.1 below threshold"
    )
    assert "knowledge_base" in explanation and "web" in explanation


def test_explanation_bundle_serializable():
    explainer = DecisionExplainer()
    profile = _make_profile()
    trace = DecisionTrace(goal_id="g1", tenant_id="t1")
    bundle = explainer.explain(profile, trace)
    json.dumps(bundle.to_dict())
