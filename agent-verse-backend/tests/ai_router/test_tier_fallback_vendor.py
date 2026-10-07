"""a01-F022-02 (superseded): with nothing configured, no role gets a vendor slug.

``_TIER_MODELS`` (OpenAI slugs) and, later, per-vendor profiles were the
fallback whenever nothing was configured in the model registry: a deployment
with only ``ANTHROPIC_API_KEY`` and no ``DEFAULT_MODEL`` had its roles sent
``gpt-4o`` (then ``claude-opus-4-8``) — models nobody configured. Every role now
resolves through ``resolve_reasoning``: with nothing configured it is ``""``
(the provider's own default model / its honest "no LLM configured" error),
whatever the vendor.
"""

from __future__ import annotations

import pytest

from app.agent.pattern_config import Complexity, Domain, GoalProperties, PatternConfig, RiskLevel
from app.ai_router.model_orchestrator import (
    ModelOrchestrator,
    ModelOrchestratorAdapter,
    model_quality_tier,
)
from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry

_ROLES = ("planning", "execution", "verification", "classification", "judge")
_ENV = (
    "NVIDIA_API_KEY", "NVIDIA_MODEL", "OPENAI_BASE_URL", "OPENAI_MODEL", "DEFAULT_MODEL",
    "DEFAULT_PLANNING_MODEL", "DEFAULT_EXECUTION_MODEL", "DEFAULT_VERIFICATION_MODEL",
)


@pytest.fixture(autouse=True)
def _nothing_configured(monkeypatch: pytest.MonkeyPatch):
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setattr(
        "app.ai_router.deployment_roles.deployment_role_models", lambda *a, **k: {}
    )
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


def _adapter(vendor: str) -> ModelOrchestratorAdapter:
    adapter = ModelOrchestratorAdapter()
    adapter.set_provider_vendor(vendor)
    return adapter


@pytest.mark.parametrize("vendor", ["anthropic", "openai", "nvidia", "vllm", "gemini", ""])
def test_no_vendor_slug_when_nothing_is_configured(vendor: str) -> None:
    for task in _ROLES:
        assert _adapter(vendor).model_for(task) == "", (vendor, task)


def test_profiled_assignment_has_no_reasoning_slug_either() -> None:
    adapter = _adapter("anthropic")
    adapter._cached_assignment = ModelOrchestrator().select_models(
        PatternConfig(
            goal_properties=GoalProperties(
                complexity=Complexity.EXPERT, domain=Domain.TECHNICAL, risk=RiskLevel.HIGH
            ),
            model_planner="", model_executor="", model_verifier="", model_classifier="",
        ),
        vendor="anthropic",
    )
    for task in _ROLES:
        assert adapter.model_for(task) == ""


def test_bound_provider_default_is_used_with_an_empty_registry() -> None:
    class _P:
        _default_model = "provider-own-model"

    adapter = _adapter("vllm")
    adapter.bind_provider(_P())
    assert adapter.model_for("planning") == "provider-own-model"


def test_plan_cap_clamps_to_a_configured_cheap_model() -> None:
    model_registry.register_configured(ModelEndpoint(
        provider="openai", model_id="gpt-4o-mini", display_name="mini",
        capabilities=[ModelCapability.TEXT_GENERATION, ModelCapability.TOOL_USE],
        supports_tools=True, cost_per_1k_input=0.00015, extra={"source": "env"},
    ))
    adapter = _adapter("openai").with_override("claude-opus-4-8")
    adapter.set_plan_tier("free")
    chosen = adapter.model_for("execution")
    assert chosen == "gpt-4o-mini"
    assert model_quality_tier(chosen) == "low"


def test_plan_cap_that_nothing_configured_meets_keeps_the_model() -> None:
    adapter = _adapter("gemini").with_override("gemini-2.0-pro")
    adapter.set_plan_tier("free")
    # Nothing configured fits the cap: a model the deployment does not serve
    # would only fail, so the pinned model stays (and it is logged).
    assert adapter.model_for("execution") == "gemini-2.0-pro"


def test_goal_service_tells_the_router_the_goal_providers_vendor() -> None:
    from app.services.goal_service import _router_vendor_of

    class AnthropicProvider:
        pass

    assert _router_vendor_of(AnthropicProvider()) == "anthropic"
    assert _router_vendor_of(None) == ""
