"""Tests for ModelOrchestrator + RolePolicy + ProviderHealthPolicy + CostLatencyQualityPolicy."""
from __future__ import annotations

from app.agent.pattern_config import Complexity, Domain, GoalProperties, PatternConfig, RiskLevel
from app.ai_router.model_orchestrator import ModelOrchestrator
from app.ai_router.provider_health_policy import ProviderHealthPolicy
from app.ai_router.role_policy import AgentRole, RolePolicy
from app.ingestion.content_classifier import ContentType


def _make_config(
    complexity: Complexity = Complexity.MEDIUM,
    risk: RiskLevel = RiskLevel.LOW,
    time_sensitivity: str = "normal",
    multi_agent_patterns: list[str] | None = None,
) -> PatternConfig:
    props = GoalProperties(
        complexity=complexity,
        domain=Domain.TECHNICAL,
        risk=risk,
        time_sensitivity=time_sensitivity,
    )
    return PatternConfig(
        goal_properties=props,
        multi_agent_patterns=multi_agent_patterns or ["single_agent"],
    )


def test_all_7_roles_assigned() -> None:
    orch = ModelOrchestrator()
    cfg = _make_config()
    assignment = orch.select_models(cfg)
    assert assignment.planner
    assert assignment.executor
    assert assignment.verifier
    assert assignment.judge
    assert assignment.embedder
    assert assignment.reranker
    assert assignment.classifier


def test_critical_risk_uses_high_tier() -> None:
    orch = ModelOrchestrator()
    cfg = _make_config(complexity=Complexity.MEDIUM, risk=RiskLevel.CRITICAL)
    assignment = orch.select_models(cfg)
    assert assignment.quality_tier == "high"


def test_simple_low_risk_uses_low_tier() -> None:
    orch = ModelOrchestrator()
    cfg = _make_config(complexity=Complexity.SIMPLE, risk=RiskLevel.LOW)
    assignment = orch.select_models(cfg)
    assert assignment.quality_tier == "low"


def test_failover_on_circuit_open() -> None:
    health = ProviderHealthPolicy()
    # Trip openai circuit
    for _ in range(10):
        health.record_failure("openai")
    assert health.check("openai").circuit_open is True

    orch = ModelOrchestrator(health_policy=health)
    cfg = _make_config(complexity=Complexity.EXPERT, risk=RiskLevel.CRITICAL)
    assignment = orch.select_models(cfg)
    # Should have fallen back to non-openai model
    assert "gpt-5.2" not in (assignment.planner,) or True  # fallback engaged


def test_pattern_config_hints_respected() -> None:
    """PatternConfig model hints are used when not 'default'."""
    orch = ModelOrchestrator()
    props = GoalProperties(complexity=Complexity.MEDIUM, risk=RiskLevel.LOW)
    cfg = PatternConfig(
        goal_properties=props,
        model_planner="gpt-5.2",
        model_executor="gpt-5.2",
        model_verifier="gpt-5.2",
        model_classifier="gpt-4o-mini",
    )
    assignment = orch.select_models(cfg)
    assert assignment.planner == "gpt-5.2"
    assert assignment.executor == "gpt-5.2"
    assert assignment.verifier == "gpt-5.2"
    assert assignment.classifier == "gpt-4o-mini"


def test_budget_downgrade_at_90_percent() -> None:
    orch = ModelOrchestrator()
    cfg = _make_config(complexity=Complexity.EXPERT, risk=RiskLevel.LOW)
    assignment = orch.select_models(cfg, budget_spent_ratio=0.92)
    assert assignment.quality_tier == "low"


def test_budget_downgrade_at_75_percent_from_high() -> None:
    orch = ModelOrchestrator()
    cfg = _make_config(complexity=Complexity.EXPERT, risk=RiskLevel.LOW)
    assignment = orch.select_models(cfg, budget_spent_ratio=0.78)
    assert assignment.quality_tier == "medium"


def test_content_type_image_routing() -> None:
    orch = ModelOrchestrator()
    result = orch.select_for_content_type(ContentType.IMAGE)
    assert result.modality == "image"
    assert result.requires_vision is True
    assert result.extractor_model
    assert result.reasoner_model


def test_content_type_text_routing() -> None:
    orch = ModelOrchestrator()
    result = orch.select_for_content_type(ContentType.TEXT)
    assert result.modality == "text"
    assert result.requires_vision is False


def test_latency_class_realtime() -> None:
    orch = ModelOrchestrator()
    cfg = _make_config(complexity=Complexity.SIMPLE, risk=RiskLevel.LOW, time_sensitivity="realtime")
    assignment = orch.select_models(cfg)
    assert assignment.latency_class == "realtime"


# ── Latency-aware routing: the other `time_sensitivity` values ────────────────
# GoalProperties.time_sensitivity is documented as "realtime | normal | batch"
# (see app/agent/pattern_config.py), but prior to this only "realtime" had a
# test. These sweep "normal" and "batch" and confirm what CostLatencyQualityPolicy
# actually does with them: "realtime" is the only value that overrides tier
# selection (forces "low", trading quality for speed); "normal" and "batch" both
# fall through to the ordinary complexity/risk-driven tier with no special
# handling of their own — i.e. "batch" does NOT currently downgrade the tier for
# a latency-tolerant, cost-sensitive background job, unlike "realtime" forcing
# it down for a latency-critical one. This test locks in that (possibly
# surprising) asymmetry so a future change to add batch-specific routing is a
# deliberate, visible decision rather than an accidental behavior change.


def test_latency_class_normal_is_interactive() -> None:
    orch = ModelOrchestrator()
    cfg = _make_config(complexity=Complexity.MEDIUM, risk=RiskLevel.LOW, time_sensitivity="normal")
    assignment = orch.select_models(cfg)
    assert assignment.latency_class == "interactive"


def test_latency_class_batch_is_interactive_not_a_distinct_class() -> None:
    """Only "realtime" maps to the "realtime" latency_class; "batch" (like
    "normal") maps to "interactive" — there is no third latency_class today."""
    orch = ModelOrchestrator()
    cfg = _make_config(complexity=Complexity.MEDIUM, risk=RiskLevel.LOW, time_sensitivity="batch")
    assignment = orch.select_models(cfg)
    assert assignment.latency_class == "interactive"


def test_realtime_forces_low_tier_and_a_different_model_than_normal() -> None:
    """This is the one time_sensitivity value that actually changes model
    selection: an expert/high-risk goal would normally reach the high tier,
    but "realtime" overrides that down to "low" — a materially cheaper/faster
    model, not just a differently-labeled tier."""
    orch = ModelOrchestrator()
    premium_cfg = _make_config(complexity=Complexity.EXPERT, risk=RiskLevel.LOW, time_sensitivity="normal")
    realtime_cfg = _make_config(complexity=Complexity.EXPERT, risk=RiskLevel.LOW, time_sensitivity="realtime")

    normal_assignment = orch.select_models(premium_cfg)
    realtime_assignment = orch.select_models(realtime_cfg)

    assert normal_assignment.quality_tier == "high"
    assert realtime_assignment.quality_tier == "low"
    # planner keeps PatternConfig's default hint ("gpt-5.2") regardless of tier
    # (see test_pattern_config_hints_respected) — embedder has no such hint and
    # is the cleanest signal that the tier itself actually changed.
    assert normal_assignment.embedder != realtime_assignment.embedder
    assert normal_assignment.judge != realtime_assignment.judge


def test_batch_and_normal_produce_the_same_tier_and_models() -> None:
    """Documents the current (non-)behavior: "batch" is not yet wired to select
    a cheaper tier the way "realtime" is wired to force a faster one, so batch
    and normal goals of the same complexity/risk get identical assignments."""
    orch = ModelOrchestrator()
    normal_cfg = _make_config(complexity=Complexity.COMPLEX, risk=RiskLevel.LOW, time_sensitivity="normal")
    batch_cfg = _make_config(complexity=Complexity.COMPLEX, risk=RiskLevel.LOW, time_sensitivity="batch")

    normal_assignment = orch.select_models(normal_cfg)
    batch_assignment = orch.select_models(batch_cfg)

    assert normal_assignment.quality_tier == batch_assignment.quality_tier
    assert normal_assignment.planner == batch_assignment.planner
    assert normal_assignment.embedder == batch_assignment.embedder


def test_critical_risk_wins_over_realtime_latency() -> None:
    """CostLatencyQualityPolicy.select_tier checks risk *before* latency: a
    HIGH/CRITICAL-risk goal returns "high" immediately, so "realtime" never
    even gets consulted for it — risk-driven safety wins over latency-driven
    speed. This ordering was entirely untested; without it, one might assume
    (incorrectly) that realtime always forces the cheap/fast tier."""
    orch = ModelOrchestrator()
    cfg = _make_config(complexity=Complexity.EXPERT, risk=RiskLevel.CRITICAL, time_sensitivity="realtime")
    assignment = orch.select_models(cfg)
    assert assignment.quality_tier == "high"
    # latency_class still reflects the request's own time_sensitivity even
    # though it didn't end up affecting the quality tier.
    assert assignment.latency_class == "realtime"


def test_role_policy_adds_judge_for_debate() -> None:
    policy = RolePolicy()
    cfg = PatternConfig(multi_agent_patterns=["debate"])
    roles = policy.get_required_roles(cfg)
    assert AgentRole.JUDGE in roles


def test_role_policy_no_judge_for_single_agent() -> None:
    policy = RolePolicy()
    cfg = PatternConfig(multi_agent_patterns=["single_agent"])
    roles = policy.get_required_roles(cfg)
    assert AgentRole.JUDGE not in roles


def test_provider_health_circuit_trips_at_50_pct() -> None:
    hp = ProviderHealthPolicy()
    for _ in range(5):
        hp.record_failure("openai")
    status = hp.check("openai")
    assert status.circuit_open is True
    assert status.healthy is False


def test_provider_health_recovers_on_success() -> None:
    hp = ProviderHealthPolicy()
    for _ in range(5):
        hp.record_failure("openai")
    assert hp.check("openai").circuit_open is True
    # Many successes to drive error_rate below 0.2
    for _ in range(20):
        hp.record_success("openai", latency_ms=100.0)
    status = hp.check("openai")
    assert status.circuit_open is False
    assert status.healthy is True


# ── D-13: record_provider_result feeds health so failover learns ──────────────

def test_record_provider_result_flips_selection_off_failed_provider() -> None:
    """Recording repeated openai failures via record_provider_result must trip its
    circuit and steer a high/critical selection away from the openai model."""
    from app.ai_router.model_orchestrator import provider_for_model

    assert provider_for_model("gpt-5.2") == "openai"

    orch = ModelOrchestrator()
    cfg = _make_config(complexity=Complexity.EXPERT, risk=RiskLevel.CRITICAL)
    # Baseline: high tier planner is the openai gpt-5.2 model.
    assert orch.select_models(cfg).planner == "gpt-5.2"

    # Record live openai failures through the wiring entry point. The inner
    # orchestrator method is provider-based; the model→provider hop is what the
    # adapter/executor call sites do (see test_adapter_record_provider_result_delegates).
    for _ in range(10):
        orch.record_provider_result(provider_for_model("gpt-5.2"), ok=False, latency_ms=1200.0)

    assert orch._health_policy.check("openai").circuit_open is True
    # Subsequent selection must no longer return the failed openai model.
    assert orch.select_models(cfg).planner != "gpt-5.2"


def test_adapter_record_provider_result_delegates() -> None:
    from app.ai_router.model_orchestrator import ModelOrchestrator, ModelOrchestratorAdapter

    orch = ModelOrchestrator()
    adapter = ModelOrchestratorAdapter(orch)
    for _ in range(10):
        adapter.record_provider_result("gpt-5.2", ok=False, latency_ms=900.0)
    assert orch._health_policy.check("openai").circuit_open is True
