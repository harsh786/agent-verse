"""Comprehensive tests for app/agent/model_router.py — targets 90%+ statement coverage."""
from __future__ import annotations

import pytest

from app.agent.model_router import (
    ModelRouter,
    ModelRouterConfig,
    _PROVIDER_DEFAULTS,
    get_router_for_tenant,
)


# ── ModelRouterConfig ────────────────────────────────────────────────────────

def test_model_router_config_defaults() -> None:
    cfg = ModelRouterConfig()
    assert cfg.planning_model == ""
    assert cfg.fallback_model == ""


def test_model_router_config_custom() -> None:
    cfg = ModelRouterConfig(planning_model="gpt-4", execution_model="gpt-3.5", fallback_model="gpt-3.5")
    assert cfg.planning_model == "gpt-4"
    assert cfg.execution_model == "gpt-3.5"


# ── _PROVIDER_DEFAULTS ────────────────────────────────────────────────────────

def test_provider_defaults_anthropic() -> None:
    cfg = _PROVIDER_DEFAULTS["anthropic"]
    assert "claude" in cfg.planning_model
    assert cfg.planning_model != cfg.execution_model


def test_provider_defaults_openai() -> None:
    cfg = _PROVIDER_DEFAULTS["openai"]
    assert "gpt" in cfg.planning_model


def test_provider_defaults_groq() -> None:
    cfg = _PROVIDER_DEFAULTS["groq"]
    assert "llama" in cfg.planning_model.lower()


def test_provider_defaults_ollama() -> None:
    cfg = _PROVIDER_DEFAULTS["ollama"]
    assert cfg.planning_model == "llama3.2"


# ── ModelRouter construction ─────────────────────────────────────────────────

def test_model_router_default_is_anthropic() -> None:
    router = ModelRouter()
    assert router._provider == "anthropic"


def test_model_router_unknown_provider_uses_empty_config() -> None:
    router = ModelRouter(provider_name="unknown_provider")
    assert router._config.planning_model == ""


def test_model_router_custom_config_overrides_defaults() -> None:
    custom = ModelRouterConfig(planning_model="custom-model", fallback_model="fallback")
    router = ModelRouter(provider_name="anthropic", config=custom)
    assert router.model_for("planning") == "custom-model"


# ── ModelRouter.model_for ────────────────────────────────────────────────────

def test_model_for_planning() -> None:
    router = ModelRouter(provider_name="anthropic")
    model = router.model_for("planning")
    assert "claude" in model.lower()


def test_model_for_execution() -> None:
    router = ModelRouter(provider_name="anthropic")
    model = router.model_for("execution")
    assert "claude" in model.lower()


def test_model_for_verification() -> None:
    router = ModelRouter(provider_name="anthropic")
    model = router.model_for("verification")
    assert "claude" in model.lower()


def test_model_for_embedding_empty_when_not_configured() -> None:
    router = ModelRouter(provider_name="anthropic")
    # Anthropic defaults don't include embedding_model
    model = router.model_for("embedding")
    # Falls back to fallback_model
    assert isinstance(model, str)


def test_model_for_classification_reuses_execution() -> None:
    router = ModelRouter(provider_name="anthropic")
    assert router.model_for("classification") == router.model_for("execution")


def test_model_for_reflection_uses_planning() -> None:
    router = ModelRouter(provider_name="anthropic")
    assert router.model_for("reflection") == router.model_for("planning")


def test_model_for_think_uses_planning() -> None:
    router = ModelRouter(provider_name="anthropic")
    assert router.model_for("think") == router.model_for("planning")


def test_model_for_thinking_uses_planning() -> None:
    router = ModelRouter(provider_name="anthropic")
    assert router.model_for("thinking") == router.model_for("planning")


def test_model_for_unknown_task_uses_fallback() -> None:
    router = ModelRouter(provider_name="anthropic")
    model = router.model_for("unknown_task_type")
    # Returns fallback
    assert isinstance(model, str)
    assert model != ""


def test_model_for_unknown_task_explicit_fallback() -> None:
    router = ModelRouter(provider_name="anthropic")
    model = router.model_for("unknown_task", fallback="explicit-fallback")
    assert model == "explicit-fallback"


def test_model_for_empty_task_uses_fallback() -> None:
    router = ModelRouter(provider_name="anthropic")
    model = router.model_for("")
    assert isinstance(model, str)


def test_model_for_openai_planning() -> None:
    router = ModelRouter(provider_name="openai")
    assert router.model_for("planning") == "gpt-4o"


def test_model_for_openai_execution() -> None:
    router = ModelRouter(provider_name="openai")
    assert router.model_for("execution") == "gpt-4o-mini"


def test_model_for_groq_verification() -> None:
    router = ModelRouter(provider_name="groq")
    assert "instant" in router.model_for("verification").lower()


def test_model_for_ollama_all_same() -> None:
    router = ModelRouter(provider_name="ollama")
    assert router.model_for("planning") == router.model_for("execution")


# ── ModelRouter.from_provider_name ───────────────────────────────────────────

def test_from_provider_name_returns_router() -> None:
    router = ModelRouter.from_provider_name("openai")
    assert isinstance(router, ModelRouter)
    assert router._provider == "openai"


# ── get_router_for_tenant ─────────────────────────────────────────────────────

def test_get_router_for_tenant_anthropic() -> None:
    router = get_router_for_tenant({"provider": "anthropic"})
    assert isinstance(router, ModelRouter)
    assert router._provider == "anthropic"


def test_get_router_for_tenant_with_default_model() -> None:
    router = get_router_for_tenant({
        "provider": "anthropic",
        "default_model": "claude-custom",
    })
    # fallback_model should be the tenant's default
    assert router._config.fallback_model == "claude-custom"


def test_get_router_for_tenant_default_model_fallback_when_base_empty() -> None:
    """Unknown provider has empty defaults — tenant default fills in."""
    router = get_router_for_tenant({
        "provider": "unknown",
        "default_model": "my-custom-model",
    })
    assert router._config.fallback_model == "my-custom-model"
    # planning_model should be default_model since base is empty
    assert router._config.planning_model == "my-custom-model"


def test_get_router_for_tenant_no_default_model_uses_base() -> None:
    router = get_router_for_tenant({"provider": "openai"})
    assert router._config.planning_model == "gpt-4o"


def test_get_router_for_tenant_missing_provider_defaults_to_anthropic() -> None:
    router = get_router_for_tenant({})
    assert router._provider == "anthropic"


def test_get_router_for_tenant_with_default_model_preserves_base_planning() -> None:
    """Base planning model is kept when provider has one configured."""
    router = get_router_for_tenant({
        "provider": "anthropic",
        "default_model": "my-override",
    })
    # Planning model should remain the anthropic default (claude-opus)
    assert "claude" in router._config.planning_model.lower()
