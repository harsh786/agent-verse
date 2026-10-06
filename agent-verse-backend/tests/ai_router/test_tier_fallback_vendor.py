"""a01-F022-02: the last-resort role models follow the goal's provider, not OpenAI.

``_TIER_MODELS`` (OpenAI slugs + voyage-3-lite) was the fallback for every
deployment whenever nothing was configured in the model registry, e.g. a
deployment with only ``ANTHROPIC_API_KEY`` and no ``DEFAULT_MODEL``: its planner,
executor and verifier were sent ``gpt-4o`` and every call failed. The adapter
now knows the goal's provider vendor (``set_provider_vendor``) and falls back to
that vendor's own profile (the same table the Celery worker's ``ModelRouter``
uses), or to "" — the provider's own default model — when it has none.
"""

from __future__ import annotations

import pytest

from app.agent.pattern_config import Complexity, Domain, GoalProperties, PatternConfig, RiskLevel
from app.ai_router.model_orchestrator import (
    _TIER_MODELS,
    ModelOrchestrator,
    ModelOrchestratorAdapter,
    model_quality_tier,
)

_OPENAI_SLUGS = {m for tier in _TIER_MODELS.values() for m in tier.values()}
_ROLES = ("planning", "execution", "verification", "classification", "judge")


@pytest.fixture(autouse=True)
def _nothing_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.ai_router.role_preference as role_preference
    import app.ai_router.selection as selection

    monkeypatch.setattr(selection, "select_configured_model_id", lambda *a, **k: "")
    monkeypatch.setattr(role_preference, "preferred_role_model", lambda *a, **k: "")
    monkeypatch.setattr(
        "app.ai_router.model_orchestrator._configured_within_cap", lambda task, cap: ""
    )
    monkeypatch.setattr("app.ai_router.model_orchestrator._configured_for_tier", lambda tier: "")
    for var in (
        "NVIDIA_API_KEY",
        "NVIDIA_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "DEFAULT_MODEL",
        "DEFAULT_PLANNING_MODEL",
        "DEFAULT_EXECUTION_MODEL",
        "DEFAULT_VERIFICATION_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


def _adapter(vendor: str) -> ModelOrchestratorAdapter:
    adapter = ModelOrchestratorAdapter()
    adapter.set_provider_vendor(vendor)
    return adapter


@pytest.mark.parametrize("task", _ROLES)
def test_anthropic_goal_never_gets_an_openai_slug(task: str) -> None:
    chosen = _adapter("anthropic").model_for(task)
    assert chosen not in _OPENAI_SLUGS
    assert chosen.startswith("claude")


@pytest.mark.parametrize("vendor", ["vllm", "fake", "gemini", "together"])
def test_vendor_without_a_profile_uses_the_providers_own_default(vendor: str) -> None:
    for task in _ROLES:
        assert _adapter(vendor).model_for(task) == ""


def test_nvidia_goal_gets_the_nvidia_profile() -> None:
    assert _adapter("nvidia").model_for("planning").startswith("nvidia/")


def test_openai_goal_keeps_the_tier_table() -> None:
    assert _adapter("openai").model_for("planning") == _TIER_MODELS["medium"]["planner"]


def test_profiled_assignment_follows_the_vendor_too() -> None:
    adapter = _adapter("anthropic")
    adapter._cached_assignment = ModelOrchestrator().select_models(
        PatternConfig(
            goal_properties=GoalProperties(
                complexity=Complexity.EXPERT, domain=Domain.TECHNICAL, risk=RiskLevel.HIGH
            ),
            model_planner="",
            model_executor="",
            model_verifier="",
            model_classifier="",  # as update_from_profile builds it
        ),
        vendor="anthropic",
    )
    for task in _ROLES:
        assert adapter.model_for(task) not in _OPENAI_SLUGS
    assert adapter._cached_assignment.embedder not in _OPENAI_SLUGS


def test_plan_cap_clamps_within_the_vendor() -> None:
    adapter = _adapter("anthropic").with_override("claude-opus-4-8")
    adapter.set_plan_tier("free")
    chosen = adapter.model_for("execution")
    assert chosen.startswith("claude")
    assert model_quality_tier(chosen) == "low"


def test_plan_cap_that_the_vendor_cannot_meet_keeps_the_model() -> None:
    adapter = _adapter("gemini").with_override("gemini-2.0-pro")
    adapter.set_plan_tier("free")
    # No cheaper model this vendor is known to serve: an OpenAI slug would only
    # fail on the Gemini provider, so the pinned model stays (and it is logged).
    assert adapter.model_for("execution") == "gemini-2.0-pro"


def test_goal_service_tells_the_router_the_goal_providers_vendor() -> None:
    from app.services.goal_service import _router_vendor_of

    class AnthropicProvider:
        pass

    assert _router_vendor_of(AnthropicProvider()) == "anthropic"
    assert _router_vendor_of(None) == ""
