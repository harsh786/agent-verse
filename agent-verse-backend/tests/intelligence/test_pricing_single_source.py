"""PROV-03: one pricing source of truth (``calculate_cost``), exact matching.

The executor budget gate used the deprecated ``governance.pricing.estimate_cost``
while the ledger used ``calculate_cost``; ``calculate_cost`` itself matched keys in
both directions (an empty or short model name matched the first key, and a dated
``gpt-4o-mini-…`` model was priced as ``gpt-4o``); ``ModelRegistry.price_for``
returned 0.0 for models the ledger charges at the fallback rate; and the
``model_pricing`` table was never read.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from app.intelligence import cost_tracker
from app.intelligence.cost_tracker import (
    MODEL_PRICING,
    calculate_cost,
    model_pricing,
    refresh_model_pricing,
    set_db_pricing,
)


@pytest.fixture(autouse=True)
def _no_db_overlay() -> Any:
    set_db_pricing({})
    yield
    set_db_pricing({})


def _fallback() -> dict[str, float]:
    return cost_tracker._fallback_pricing()


def test_empty_model_uses_fallback_not_first_key() -> None:
    fb = _fallback()
    assert calculate_cost("", 1_000_000, 0) == pytest.approx(fb["input"])
    assert model_pricing("") == (fb["input"], fb["output"])


def test_short_model_does_not_match_longer_key() -> None:
    # "gpt-4" is not "gpt-4.5-preview" / "gpt-4o" / "gpt-4-turbo".
    fb = _fallback()
    assert calculate_cost("gpt-4", 1_000_000, 0) == pytest.approx(fb["input"])


def test_dated_model_matches_its_own_base_not_a_shorter_sibling() -> None:
    mini = MODEL_PRICING["gpt-4o-mini"]
    assert calculate_cost("gpt-4o-mini-2024-07-18", 1_000_000, 0) == pytest.approx(
        mini["input"]
    )
    sonnet = MODEL_PRICING["claude-sonnet-4-5"]
    assert calculate_cost("claude-sonnet-4-5-20250929", 0, 1_000_000) == pytest.approx(
        sonnet["output"]
    )


def test_prefix_needs_a_separator_boundary() -> None:
    # "gpt-4o" must not price "gpt-4omni-x" as gpt-4o.
    fb = _fallback()
    assert calculate_cost("gpt-4omni-x", 1_000_000, 0) == pytest.approx(fb["input"])


def test_provider_prefixed_slug_and_case_are_normalised() -> None:
    assert calculate_cost("openai/GPT-4o", 1_000_000, 0) == pytest.approx(
        MODEL_PRICING["gpt-4o"]["input"]
    )


def test_db_pricing_overrides_reference_table() -> None:
    set_db_pricing({"gpt-4o": (1.0, 2.0), "my-onprem-model": (0.1, 0.2)})
    assert calculate_cost("gpt-4o", 1_000_000, 1_000_000) == pytest.approx(3.0)
    assert calculate_cost("my-onprem-model-v2", 1_000_000, 0) == pytest.approx(0.1)


async def test_refresh_model_pricing_reads_the_model_pricing_table() -> None:
    class _Result:
        def all(self) -> list[tuple[str, float, float]]:
            return [("my-model", 0.5, 1.5)]

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        async def execute(self, stmt: Any, *a: object) -> _Result:
            assert "model_pricing" in str(stmt)
            return _Result()

    n = await refresh_model_pricing(lambda: _Session(), force=True)
    assert n == 1
    assert calculate_cost("my-model", 1_000_000, 1_000_000) == pytest.approx(2.0)


async def test_refresh_model_pricing_failure_keeps_the_previous_table() -> None:
    set_db_pricing({"my-model": (0.5, 1.5)})

    def _broken() -> Any:
        raise RuntimeError("db down")

    assert await refresh_model_pricing(_broken, force=True) == 0
    assert calculate_cost("my-model", 1_000_000, 0) == pytest.approx(0.5)


def test_registry_price_for_agrees_with_the_ledger() -> None:
    from app.ai_router.registry import ModelRegistry

    reg = ModelRegistry()
    fb = _fallback()
    # Unknown / self-hosted: the fallback the ledger charges, not 0.0.
    assert reg.price_for("some/selfhosted-model") == pytest.approx(
        (fb["input"] / 1000, fb["output"] / 1000)
    )
    gpt = MODEL_PRICING["gpt-4o"]
    assert reg.price_for("gpt-4o") == pytest.approx((gpt["input"] / 1000, gpt["output"] / 1000))


def test_deprecated_estimate_cost_is_gone() -> None:
    pricing = importlib.import_module("app.governance.pricing")
    assert not hasattr(pricing, "estimate_cost")
