"""Tests for app/governance/pricing.py — 8 tests."""
from __future__ import annotations

import pytest

from app.governance.pricing import estimate_cost, format_cost


# ---------------------------------------------------------------------------
# Cost comparisons
# ---------------------------------------------------------------------------

def test_claude_opus_more_expensive_than_haiku() -> None:
    """claude-opus-4 should cost significantly more than claude-haiku per token."""
    opus_cost = estimate_cost("claude-opus-4", 1000, 1000)
    haiku_cost = estimate_cost("claude-haiku", 1000, 1000)
    assert opus_cost > haiku_cost


def test_gpt4o_mini_cheaper_than_gpt4o() -> None:
    """gpt-4o-mini must be cheaper than gpt-4o."""
    mini_cost = estimate_cost("gpt-4o-mini", 1000, 1000)
    full_cost = estimate_cost("gpt-4o", 1000, 1000)
    assert mini_cost < full_cost


def test_estimate_cost_returns_positive_float_for_known_model() -> None:
    cost = estimate_cost("claude-sonnet-4", 500, 200)
    assert isinstance(cost, float)
    assert cost > 0.0


def test_unknown_model_uses_fallback_non_zero() -> None:
    """An unknown model string should still return a non-zero conservative estimate."""
    cost = estimate_cost("totally-unknown-model-xyz-9999", 1000, 1000)
    assert cost > 0.0


def test_zero_tokens_returns_zero() -> None:
    cost = estimate_cost("gpt-4o", 0, 0)
    assert cost == 0.0


# ---------------------------------------------------------------------------
# format_cost
# ---------------------------------------------------------------------------

def test_format_cost_tiny_amount_has_four_decimal_places() -> None:
    result = format_cost(0.0012)
    assert result == "$0.0012"


def test_format_cost_larger_amount_has_two_decimal_places() -> None:
    result = format_cost(1.23456)
    assert result == "$1.23"


# ---------------------------------------------------------------------------
# Pricing is per-1k-tokens, not per-token
# ---------------------------------------------------------------------------

def test_pricing_is_per_1k_tokens_not_per_token() -> None:
    """1000 input tokens should cost 1× the per-1k rate, 2000 should cost 2×."""
    cost_1k = estimate_cost("gpt-4o", 1000, 0)
    cost_2k = estimate_cost("gpt-4o", 2000, 0)
    assert abs(cost_2k - 2 * cost_1k) < 1e-9
