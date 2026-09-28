"""Regression tests: guardrails v2 tenant regex is bounded; toxicity can trigger.

1. Tenant ``regex_match`` patterns ran unbounded (``re.findall``): a
   catastrophic-backtracking rule pinned the worker CPU on every message, and an
   invalid pattern silently passed all content.
2. The toxicity rule never triggered without an LLM provider, or on a provider
   error.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.guardrails_v2.engine import GuardrailsEngine


def test_catastrophic_regex_is_cut_off_and_fails_closed() -> None:
    engine = GuardrailsEngine()
    start = time.monotonic()
    result = engine._check_regex("a" * 60 + "b", {"pattern": r"(a|aa)+$"})
    assert time.monotonic() - start < 5.0
    assert result["triggered"] is True
    assert result["matches"] == ["regex_timeout"]


def test_invalid_tenant_regex_fails_closed() -> None:
    result = GuardrailsEngine()._check_regex("anything", {"pattern": "(unclosed"})
    assert result["triggered"] is True


def test_normal_regex_still_matches() -> None:
    result = GuardrailsEngine()._check_regex("order #1234", {"pattern": r"#\d+"})
    assert result == {"triggered": True, "matches": ["#1234"], "category": "regex"}


@pytest.mark.asyncio
async def test_toxicity_without_provider_uses_the_pattern_classifier() -> None:
    engine = GuardrailsEngine()
    engine._provider = None
    clean = await engine._check_toxicity_llm("Please summarise the quarterly report.")
    assert clean["triggered"] is False
    # The pattern classifier is the floor: content it flags must trigger.
    from app.guardrails_v2.toxicity import ToxicityClassifier

    toxic_text = "I will kill you, you worthless idiot"
    expected = ToxicityClassifier(use_llm_for_ambiguous=False).classify_sync(toxic_text).is_toxic
    got = await engine._check_toxicity_llm(toxic_text)
    assert got["triggered"] is expected


@pytest.mark.asyncio
async def test_toxicity_provider_error_falls_back_to_patterns() -> None:
    engine = GuardrailsEngine()
    provider = MagicMock()
    provider.complete = AsyncMock(side_effect=RuntimeError("provider down"))
    engine._provider = provider
    patterns = MagicMock(return_value={"triggered": True, "matches": [], "category": "toxicity"})
    engine._check_toxicity_patterns = patterns  # type: ignore[method-assign]
    assert (await engine._check_toxicity_llm("x"))["triggered"] is True
    patterns.assert_called_once()
