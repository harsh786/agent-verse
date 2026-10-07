"""ModelRouter: every role resolves through resolve_reasoning — no vendor profiles.

The router used to carry built-in per-vendor model profiles (``claude-opus-4-8``
for Anthropic planning, ``gpt-4o-mini`` for OpenAI execution, ...). A goal on a
deployment that did not serve those slugs was routed to them anyway. Now the
router only adds the per-goal context (override, tenant pin, role map, an
agent's own configured model, the bound provider) to the ONE reasoning resolver.
"""

from __future__ import annotations

import pytest

from app.agent.model_router import ModelRouter, ModelRouterConfig, get_router_for_tenant


class _Provider:
    def __init__(self, default: str, *, byok: str | None = None) -> None:
        self._default_model = default
        if byok:
            self._byok_tenant_id = byok


def test_no_vendor_profile_slugs_when_nothing_is_configured() -> None:
    for vendor in ("anthropic", "openai", "groq", "ollama", "nvidia", "onprem", "hybrid", ""):
        router = ModelRouter(vendor)
        for task in ("planning", "execution", "verification", "classification", "judge"):
            assert router.model_for(task) == "", (vendor, task)


def test_explicit_fallback_when_nothing_is_configured() -> None:
    assert ModelRouter("anthropic").model_for("planning", fallback="mine") == "mine"


def test_env_default_model_serves_every_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_MODEL", "env-model")
    router = ModelRouter("anthropic")
    for task in ("planning", "execution", "verification", "classification", "judge",
                 "reflection", "think", "supervisor", "refine"):
        assert router.model_for(task) == "env-model", task


def test_role_env_pins_win_over_the_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_MODEL", "env-model")
    monkeypatch.setenv("DEFAULT_PLANNING_MODEL", "plan-pin")
    monkeypatch.setenv("DEFAULT_EXECUTION_MODEL", "exec-pin")
    router = ModelRouter()
    assert router.model_for("planning") == "plan-pin"
    assert router.model_for("execution") == "exec-pin"
    assert router.model_for("classification") == "exec-pin"
    assert router.model_for("verification") == "env-model"


def test_agent_config_is_the_agents_own_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_MODEL", "env-model")
    router = ModelRouter(config=ModelRouterConfig(planning_model="agent-plan",
                                                  fallback_model="agent-any"))
    assert router.model_for("planning") == "agent-plan"
    assert router.model_for("execution") == "agent-any"


def test_override_and_tenant_pin_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_MODEL", "env-model")
    router = ModelRouter(config=ModelRouterConfig(fallback_model="agent-any"))
    router.set_policy_roles({"planning": "tenant-pin"})
    assert router.model_for("planning") == "tenant-pin"
    assert router.model_for("supervisor") == "tenant-pin"
    assert router.model_for("execution") == "agent-any"
    pinned = router.with_override("goal-override")
    assert pinned.model_for("planning") == "goal-override"
    assert router.model_for("planning") == "tenant-pin"  # copy-on-write


def test_role_map_between_env_pins_and_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_MODEL", "env-model")
    router = ModelRouter("hybrid")
    router.set_role_map({"planning": "cloud-top", "execution": "local-qwen"})
    assert router.model_for("planning") == "cloud-top"
    assert router.model_for("classification") == "local-qwen"
    assert router.model_for("verification") == "env-model"


def test_bound_provider_default_only_with_an_empty_registry() -> None:
    router = ModelRouter()
    router.bind_provider(_Provider("provider-default"))
    assert router.model_for("planning") == "provider-default"


def test_byok_provider_keeps_its_own_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_MODEL", "env-model")
    router = ModelRouter()
    router.bind_provider(_Provider("tenant-claude", byok="t1"))
    assert router.model_for("planning") == "tenant-claude"
    assert router.with_override("explicit").model_for("planning") == "explicit"


def test_embedding_is_not_a_reasoning_role() -> None:
    # Embeddings use the Model Registry embedder, never a router role field.
    from dataclasses import fields

    assert "embedding_model" not in {f.name for f in fields(ModelRouterConfig)}


def test_get_router_for_tenant_default_model_serves_every_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEFAULT_MODEL", "env-model")
    router = get_router_for_tenant({"provider": "openai", "default_model": "tenant-model"})
    assert router._config.fallback_model == "tenant-model"
    assert router.model_for("planning") == "tenant-model"
    assert get_router_for_tenant({"provider": "openai"}).model_for("planning") == "env-model"


def test_from_provider_name() -> None:
    router = ModelRouter.from_provider_name("groq")
    assert isinstance(router, ModelRouter)
    assert router._provider == "groq"
