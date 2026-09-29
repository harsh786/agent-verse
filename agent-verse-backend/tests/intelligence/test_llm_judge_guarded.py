"""LLMJudge goes through the charged, circuit-broken decision path, fail-closed."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.intelligence.guardrail_engine import LLMJudge
from app.providers import guarded_completion as gc
from tests.providers._decision_fakes import RecordingController, ScriptedProvider

_TEXT = "Please summarise the quarterly revenue report for the finance team."


@pytest.fixture(autouse=True)
def _platform() -> Any:
    saved = gc._platform_services
    yield
    gc.set_platform_cost_services(saved)


def _judge(provider: ScriptedProvider) -> LLMJudge:
    async def factory() -> ScriptedProvider:
        return provider

    return LLMJudge(provider_factory=factory, model="")


async def test_judge_call_is_charged_to_the_tenant() -> None:
    ctrl = RecordingController()
    gc.set_platform_cost_services(lambda: (ctrl, None))
    provider = ScriptedProvider('{"risk_score": 0.1, "primary_risk_type": "benign"}')
    assert await _judge(provider).evaluate(_TEXT, tenant_id="t1") is None
    assert [t for _, t in ctrl.recorded] == ["t1"]


async def test_hung_judge_fails_closed_quickly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTVERSE_DECISION_CALL_TIMEOUT_SECONDS", "0.05")
    violation = await asyncio.wait_for(
        _judge(ScriptedProvider(hang=True)).evaluate(_TEXT, tenant_id="t1"), timeout=5
    )
    assert violation is not None and violation.risk_score == 0.8
    assert violation.category == "llm_judge_error_fail_closed"


async def test_budget_refusal_fails_closed_without_calling_the_model() -> None:
    gc.set_platform_cost_services(lambda: (RecordingController(remaining=False), None))
    provider = ScriptedProvider('{"risk_score": 0.0}')
    violation = await _judge(provider).evaluate(_TEXT, tenant_id="t1")
    assert violation is not None and violation.risk_score == 0.8
    assert provider.requests == []
