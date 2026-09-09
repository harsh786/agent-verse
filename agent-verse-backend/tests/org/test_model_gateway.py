"""Tests for ModelGateway — app/org/model_gateway.py"""
from __future__ import annotations

from app.org.model_gateway import MODEL_PROFILES, ModelGateway, ModelSelection


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


async def test_gateway_coding_task_selects_coding_model():
    gw = ModelGateway()
    sel = await gw.select_model(role_profile="coding", task_type="code_generation")
    assert "sonnet" in sel.model_id.lower()


async def test_gateway_fallback_cascade_on_unavailable():
    gw = ModelGateway()
    # Force the primary that would otherwise win to be unavailable.
    baseline = await gw.select_model(role_profile="premium", task_type="strategy", quality_req=0.99)
    await gw.mark_unhealthy(baseline.model_id)
    sel = await gw.select_model(role_profile="premium", task_type="strategy", quality_req=0.99)
    assert sel.model_id != baseline.model_id
    assert sel.model_id  # fallback was found


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


async def test_gateway_privacy_constraint_blocks_external():
    gw = ModelGateway()
    sel = await gw.select_model(
        role_profile="smart",
        task_type="contract_review",
        has_pii=True,
    )
    # Privacy required → should not route to an OpenAI ("gpt") model.
    assert sel.model_id
    assert "gpt" not in sel.model_id.lower()


async def test_gateway_returns_selection_metadata():
    gw = ModelGateway()
    sel = await gw.select_model(role_profile="analytical", task_type="sql_analysis")
    assert sel.model_id
    assert sel.profile_name
    assert sel.reasoning
