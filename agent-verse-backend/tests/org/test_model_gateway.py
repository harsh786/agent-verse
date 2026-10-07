"""Tests for ModelGateway — app/org/model_gateway.py.

Profiles name a reasoning ROLE; the model comes from the Model Registry via
resolve_reasoning (the profiles used to hardcode claude-sonnet-4-5 / gpt-4o /
gpt-4o-mini, so the org collaboration tick always sent gpt-4o-mini).
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.org.model_gateway import MODEL_PROFILES, ModelGateway, ModelSelection

_ORDER = [("onprem", "local-qwen"), ("anthropic", "claude-sonnet-4-5"), ("openai", "gpt-4o")]


@pytest.fixture(autouse=True)
def _registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setattr(
        "app.ai_router.deployment_roles.deployment_role_models", lambda *a, **k: {}
    )
    for var in ("DEFAULT_MODEL", "NVIDIA_MODEL", "OPENAI_MODEL", "DEFAULT_PLANNING_MODEL",
                "DEFAULT_EXECUTION_MODEL", "DEFAULT_VERIFICATION_MODEL"):
        monkeypatch.delenv(var, raising=False)
    model_registry.clear_configured()
    for provider, model_id in _ORDER:
        model_registry.register_configured(ModelEndpoint(
            provider=provider, model_id=model_id, display_name=model_id,
            capabilities=[ModelCapability.TEXT_GENERATION, ModelCapability.TOOL_USE],
            supports_tools=True, extra={"source": "env"},
        ))
    model_registry.set_preferences({"text_generation": [f"{p}/{m}" for p, m in _ORDER]})
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


async def test_gateway_executive_gets_premium_model():
    gw = ModelGateway()
    sel = await gw.select_model(role_profile="premium", task_type="strategy", quality_req=0.95)
    assert isinstance(sel, ModelSelection)
    # A high quality requirement must be satisfied by a genuinely high-quality profile.
    assert MODEL_PROFILES[sel.profile_name].min_quality >= 0.90


async def test_gateway_support_gets_fast_model():
    gw = ModelGateway()
    sel = await gw.select_model(role_profile="fast", task_type="answer", latency_budget_ms=2000)
    assert sel.estimated_latency_s <= 5.0


async def test_gateway_model_is_the_registry_model_for_the_profile_role():
    gw = ModelGateway()
    sel = await gw.select_model(role_profile="coding", task_type="code_generation")
    assert sel.model_id == "local-qwen"  # ranked first in the Model Registry
    assert sel.fallback_model == "claude-sonnet-4-5"  # the rest of the order


async def test_gateway_fallback_cascade_on_unavailable():
    gw = ModelGateway()
    baseline = await gw.select_model(role_profile="premium", task_type="strategy", quality_req=0.99)
    await gw.mark_unhealthy(baseline.model_id)
    sel = await gw.select_model(role_profile="premium", task_type="strategy", quality_req=0.99)
    assert sel.model_id != baseline.model_id
    assert sel.model_id  # the next model of the registry order


async def test_gateway_respects_cost_budget():
    gw = ModelGateway()
    sel = await gw.select_model(
        role_profile="marketing",
        task_type="draft",
        quality_req=0.65,
        cost_budget_usd=0.001,
    )
    assert sel.estimated_cost_usd_per_1k <= 0.001
    assert MODEL_PROFILES[sel.profile_name].cost_tier == "economy"


async def test_gateway_privacy_constraint_blocks_openai():
    gw = ModelGateway()
    await gw.mark_unhealthy("local-qwen")
    await gw.mark_unhealthy("claude-sonnet-4-5")
    sel = await gw.select_model(role_profile="smart", task_type="contract_review", has_pii=True)
    # Privacy required → an OpenAI model is never routed to, even as the last one left.
    assert sel.model_id == ""


async def test_gateway_returns_selection_metadata():
    gw = ModelGateway()
    sel = await gw.select_model(role_profile="analytical", task_type="sql_analysis")
    assert sel.model_id
    assert sel.profile_name
    assert sel.reasoning


async def test_nothing_configured_yields_no_model_never_a_slug():
    model_registry.clear_configured()
    model_registry.set_preferences({})
    sel = await ModelGateway().select_model(role_profile="fast", task_type="chatter")
    assert sel.model_id == ""
    assert "unconfigured" in sel.reasoning
