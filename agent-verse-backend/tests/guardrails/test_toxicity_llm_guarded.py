"""Guardrails v2 toxicity LLM checks are charged and circuit-broken."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.guardrails_v2.toxicity import ToxicityClassifier
from app.providers import guarded_completion as gc
from tests.providers._decision_fakes import RecordingController, ScriptedProvider


@pytest.fixture(autouse=True)
def _platform() -> Any:
    saved = gc._platform_services
    yield
    gc.set_platform_cost_services(saved)


async def test_engine_toxicity_llm_is_charged_to_the_rule_tenant() -> None:
    from app.guardrails_v2.engine import GuardrailsEngine

    ctrl = RecordingController()
    gc.set_platform_cost_services(lambda: (ctrl, None))
    engine = GuardrailsEngine()
    engine._provider = ScriptedProvider("no")
    result = await engine._check_toxicity_llm("a perfectly polite message", tenant_id="t9")
    assert result["triggered"] is False
    assert [t for _, t in ctrl.recorded] == ["t9"]


async def test_engine_hung_toxicity_llm_falls_back_to_patterns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.guardrails_v2.engine import GuardrailsEngine

    monkeypatch.setenv("AGENTVERSE_DECISION_CALL_TIMEOUT_SECONDS", "0.05")
    engine = GuardrailsEngine()
    engine._provider = ScriptedProvider(hang=True)
    result = await asyncio.wait_for(
        engine._check_toxicity_llm("a perfectly polite message", tenant_id="t9"), timeout=5
    )
    assert result["category"] == "toxicity"


async def test_classifier_llm_check_is_charged() -> None:
    ctrl = RecordingController()
    gc.set_platform_cost_services(lambda: (ctrl, None))
    result = await ToxicityClassifier()._llm_check(
        "text", ScriptedProvider('{"score": 0.1, "categories": []}'), tenant_id="t9"
    )
    assert result.method == "llm" and result.score == 0.1
    assert [t for _, t in ctrl.recorded] == ["t9"]
