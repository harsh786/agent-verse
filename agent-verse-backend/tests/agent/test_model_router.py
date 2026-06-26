"""Tests for multi-model routing."""
from app.agent.model_router import ModelRouter, ModelRouterConfig, get_router_for_tenant


def test_anthropic_defaults():
    router = ModelRouter("anthropic")
    assert router.model_for("planning") == "claude-opus-4-8"
    assert router.model_for("execution") == "claude-sonnet-4-5"
    assert router.model_for("verification") == "claude-haiku-3-5"


def test_openai_defaults():
    router = ModelRouter("openai")
    assert router.model_for("planning") == "gpt-4o"
    assert router.model_for("execution") == "gpt-4o-mini"


def test_unknown_task_type_falls_back():
    router = ModelRouter("anthropic")
    model = router.model_for("unknown_task", fallback="my-fallback")
    assert model == "my-fallback"


def test_get_router_for_tenant_with_model():
    router = get_router_for_tenant({"provider": "openai", "default_model": "gpt-5"})
    # Fallback should be gpt-5
    assert router._config.fallback_model == "gpt-5"


def test_from_provider_name():
    router = ModelRouter.from_provider_name("groq")
    assert "llama" in router.model_for("planning").lower()


def test_model_for_classification_uses_execution_model():
    router = ModelRouter("openai")
    assert router.model_for("classification") == router.model_for("execution")
