"""Shared fixtures for the ai_router tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

# Reasoning models a "tiered" deployment configured in its Model Registry. The
# routers have no built-in model tables any more: tests that exercise quality
# tiers / plan caps / failover register real priced models (the pricing table
# decides each one's tier) instead of relying on hardcoded slugs.
TIERED_MODELS: dict[str, tuple[str, float, float]] = {
    # model_id: (provider, cost per 1k input, quality)
    "gpt-4o-mini": ("openai", 0.00015, 0.70),
    "gpt-4o": ("openai", 0.0025, 0.85),
    "gpt-5.2": ("openai", 0.010, 0.95),
    "claude-sonnet-4-5": ("anthropic", 0.003, 0.80),
    "gemini-2.5-pro": ("google", 0.00125, 0.80),
}

_ROUTING_ENV = (
    "NVIDIA_API_KEY", "NVIDIA_MODEL", "OPENAI_BASE_URL", "OPENAI_MODEL", "DEFAULT_MODEL",
    "DEFAULT_PLANNING_MODEL", "DEFAULT_EXECUTION_MODEL", "DEFAULT_VERIFICATION_MODEL",
)


@pytest.fixture
def tiered_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A registry holding :data:`TIERED_MODELS` (and nothing from the ambient env)."""
    import app.ai_router.selection as sel
    from app.ai_router.models import ModelCapability, ModelEndpoint
    from app.ai_router.registry import model_registry

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setattr(
        "app.ai_router.deployment_roles.deployment_role_models", lambda *a, **k: {}
    )
    for var in _ROUTING_ENV:
        monkeypatch.delenv(var, raising=False)
    model_registry.clear_configured()
    model_registry.set_preferences({})
    for model_id, (provider, cost, quality) in TIERED_MODELS.items():
        vision = model_id != "gpt-4o-mini" and model_id != "gpt-5.2"
        caps = [ModelCapability.TEXT_GENERATION, ModelCapability.TOOL_USE]
        model_registry.register_configured(ModelEndpoint(
            provider=provider, model_id=model_id, display_name=model_id,
            capabilities=caps + ([ModelCapability.VISION] if vision else []),
            supports_tools=True, supports_vision=vision, cost_per_1k_input=cost,
            quality_score=quality, extra={"source": "env"},
        ))
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})
