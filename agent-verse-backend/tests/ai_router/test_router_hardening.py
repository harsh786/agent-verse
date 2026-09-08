"""Hardening tests for the multi-AI model router (Coverage-Matrix 7b, D-13/D-14).

D-13 — the circuit-breaker/failover must actually fire once provider results are
recorded through ModelOrchestrator's public API, routing selection to a *different*
provider (not another model from the dead provider).

D-14 — vision/audio content-type selection must return a modality-correct assignment
and preserve the required capability across a provider failover.
"""
from __future__ import annotations

from app.agent.pattern_config import (
    Complexity,
    Domain,
    GoalProperties,
    PatternConfig,
    RiskLevel,
)
from app.ai_router.model_orchestrator import (
    _MODEL_PROVIDER,
    ModelOrchestrator,
)
from app.ai_router.provider_health_policy import ProviderHealthPolicy
from app.ingestion.content_classifier import ContentType


def _high_tier_config() -> PatternConfig:
    props = GoalProperties(
        complexity=Complexity.EXPERT,
        domain=Domain.TECHNICAL,
        risk=RiskLevel.CRITICAL,
        time_sensitivity="normal",
    )
    return PatternConfig(goal_properties=props, multi_agent_patterns=["single_agent"])


def _provider_of(model: str) -> str:
    return _MODEL_PROVIDER.get(model, "openai")


# ── D-13: record_provider_result drives the circuit breaker ───────────────────


def test_record_provider_result_exists_and_delegates() -> None:
    """Public API the executor/verifier/planner mixin can call at the provider-call site."""
    hp = ProviderHealthPolicy()
    orch = ModelOrchestrator(health_policy=hp)
    for _ in range(5):
        orch.record_provider_result("openai", ok=False)
    assert hp.check("openai").circuit_open is True


def test_failover_routes_to_a_different_provider_after_failures() -> None:
    """After N recorded failures the dead provider is skipped and a fallback provider chosen."""
    orch = ModelOrchestrator()
    cfg = _high_tier_config()

    # Healthy: high tier planner is an openai model (gpt-5.2).
    healthy = orch.select_models(cfg)
    assert _provider_of(healthy.planner) == "openai"

    # Trip openai.
    for _ in range(6):
        orch.record_provider_result("openai", ok=False)

    degraded = orch.select_models(cfg)
    # Must route AWAY from openai — not merely to another openai model.
    assert _provider_of(degraded.planner) != "openai", degraded.planner
    assert _provider_of(degraded.executor) != "openai", degraded.executor
    assert _provider_of(degraded.verifier) != "openai", degraded.verifier


def test_provider_recovers_and_primary_is_used_again() -> None:
    """After recovery the primary provider's model is selected again."""
    orch = ModelOrchestrator()
    cfg = _high_tier_config()

    for _ in range(6):
        orch.record_provider_result("openai", ok=False)
    assert _provider_of(orch.select_models(cfg).planner) != "openai"

    # Drive error_rate back below the recovery threshold.
    for _ in range(12):
        orch.record_provider_result("openai", ok=True, latency_ms=80.0)

    recovered = orch.select_models(cfg)
    assert _provider_of(recovered.planner) == "openai", recovered.planner


def test_provider_for_model_helper() -> None:
    orch = ModelOrchestrator()
    assert orch.provider_for_model("gpt-5.2") == "openai"
    assert orch.provider_for_model("claude-3-5-sonnet") == "anthropic"
    assert orch.provider_for_model("gemini-2.5-pro") == "google"
    # Unknown model defaults to openai (safe default).
    assert orch.provider_for_model("totally-unknown") == "openai"


def test_both_providers_down_returns_a_usable_model() -> None:
    """When primary and fallback are both open, selection still yields a non-empty model."""
    orch = ModelOrchestrator()
    for _ in range(6):
        orch.record_provider_result("openai", ok=False)
        orch.record_provider_result("anthropic", ok=False)
        orch.record_provider_result("google", ok=False)
    assignment = orch.select_models(_high_tier_config())
    assert assignment.planner
    assert assignment.executor
    assert assignment.verifier


# ── D-14: vision/audio content-type selection ─────────────────────────────────


def test_image_selects_vision_capable_model() -> None:
    orch = ModelOrchestrator()
    a = orch.select_for_content_type(ContentType.IMAGE)
    assert a.modality == "image"
    assert a.requires_vision is True
    assert a.requires_audio is False
    # extractor must be a vision-capable model.
    assert a.extractor_model in {"gpt-4o", "claude-3-5-sonnet", "gemini-2.5-pro"}


def test_audio_selects_audio_capable_model() -> None:
    orch = ModelOrchestrator()
    a = orch.select_for_content_type(ContentType.AUDIO)
    assert a.modality == "audio"
    assert a.requires_audio is True
    assert a.requires_vision is False
    assert a.extractor_model, "audio extractor must be assigned"
    # audio extractor must be an audio-capable model.
    assert a.extractor_model in {"gpt-4o-audio", "gemini-2.5-pro"}


def test_audio_failover_preserves_audio_capability() -> None:
    """When the primary audio provider is down, the extractor stays audio-capable."""
    orch = ModelOrchestrator()
    for _ in range(6):
        orch.record_provider_result("openai", ok=False)
    a = orch.select_for_content_type(ContentType.AUDIO)
    assert a.requires_audio is True
    assert a.extractor_model in {"gemini-2.5-pro"}, a.extractor_model
    assert _provider_of(a.extractor_model) != "openai"


def test_image_failover_preserves_vision_capability() -> None:
    orch = ModelOrchestrator()
    for _ in range(6):
        orch.record_provider_result("openai", ok=False)
    a = orch.select_for_content_type(ContentType.IMAGE)
    assert a.requires_vision is True
    assert a.extractor_model in {"claude-3-5-sonnet", "gemini-2.5-pro"}, a.extractor_model
    assert _provider_of(a.extractor_model) != "openai"


def test_text_needs_no_vision() -> None:
    orch = ModelOrchestrator()
    a = orch.select_for_content_type(ContentType.TEXT)
    assert a.modality == "text"
    assert a.requires_vision is False
    assert a.requires_audio is False


def test_all_content_types_get_a_valid_assignment() -> None:
    orch = ModelOrchestrator()
    for ct in ContentType:
        a = orch.select_for_content_type(ct)
        assert a.extractor_model, f"{ct.value}: extractor empty"
        assert a.reasoner_model, f"{ct.value}: reasoner empty"
