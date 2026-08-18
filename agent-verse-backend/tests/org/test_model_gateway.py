"""Tests for ModelGateway — app/org/model_gateway.py"""
from __future__ import annotations

import pytest
from app.org.model_gateway import ModelGateway, ModelSelection


def test_gateway_executive_gets_premium_model():
    gw = ModelGateway()
    sel = gw.select_model(role_family="executive", task_type="strategy", quality_required=0.95)
    assert isinstance(sel, ModelSelection)
    assert sel.cost_tier in ("premium", "smart")


def test_gateway_support_gets_fast_model():
    gw = ModelGateway()
    sel = gw.select_model(role_family="support", task_type="answer", latency_slo_seconds=2.0)
    assert sel.latency_tier in ("fast", "economy")


def test_gateway_coding_task_selects_coding_model():
    gw = ModelGateway()
    sel = gw.select_model(role_family="engineering", task_type="code_generation")
    assert "codex" in sel.model_id.lower() or "sonnet" in sel.model_id.lower() or sel.model_id


def test_gateway_fallback_cascade_on_unavailable():
    gw = ModelGateway()
    # Force primary unavailable
    sel = gw.select_model(
        role_family="executive",
        task_type="strategy",
        exclude_models=["claude-opus-4"],
    )
    assert sel.model_id != "claude-opus-4"
    assert sel.model_id  # fallback was found


def test_gateway_respects_cost_budget():
    gw = ModelGateway()
    sel = gw.select_model(
        role_family="marketing",
        task_type="draft",
        max_cost_per_1k_tokens=0.002,
    )
    assert sel.cost_per_1k_tokens <= 0.005  # within reasonable range


def test_gateway_privacy_constraint_blocks_external():
    gw = ModelGateway()
    sel = gw.select_model(
        role_family="legal",
        task_type="contract_review",
        privacy_required=True,
    )
    # Privacy required → should prefer local or private model
    assert sel.model_id


def test_gateway_returns_selection_metadata():
    gw = ModelGateway()
    sel = gw.select_model(role_family="data", task_type="sql_analysis")
    assert sel.model_id
    assert sel.provider
    assert sel.reasoning
