"""Eval scorers are charged to the evaluated goal and bounded by the circuit."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.intelligence.eval_runner import EvalRunner
from app.providers import guarded_completion as gc
from tests.providers._decision_fakes import RecordingController, ScriptedProvider


@pytest.fixture(autouse=True)
def _platform() -> Any:
    saved = gc._platform_services
    yield
    gc.set_platform_cost_services(saved)


class _Tenant:
    tenant_id = "t1"


async def test_scorers_charge_the_goal_being_evaluated() -> None:
    ctrl = RecordingController()
    gc.set_platform_cost_services(lambda: (ctrl, None))
    runner = EvalRunner()
    provider = ScriptedProvider("0.9")
    coherence = await runner._score_coherence(
        "goal", ["step one"], provider, tenant_ctx=_Tenant(), goal_id="g-42"
    )
    accuracy = await runner._score_accuracy(
        "goal", [], "ok", True, provider, tenant_ctx=_Tenant(), goal_id="g-42"
    )
    assert coherence == 0.9 and accuracy == 0.9
    assert ctrl.recorded == [("g-42", "t1"), ("g-42", "t1")]


async def test_hung_scorer_returns_the_conservative_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTVERSE_DECISION_CALL_TIMEOUT_SECONDS", "0.05")
    runner = EvalRunner()
    provider = ScriptedProvider(hang=True)
    coherence = await asyncio.wait_for(
        runner._score_coherence("goal", ["s"], provider, tenant_ctx=_Tenant()), timeout=5
    )
    accuracy = await asyncio.wait_for(
        runner._score_accuracy("goal", [], "", False, provider, tenant_ctx=_Tenant()), timeout=5
    )
    assert coherence == 0.7 and accuracy == 0.0
