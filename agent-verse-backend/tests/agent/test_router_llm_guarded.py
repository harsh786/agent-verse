"""AgentRouter LLM scoring is charged and circuit-broken; failures fall back."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.agent.router import AgentRouter
from app.providers import guarded_completion as gc
from app.tenancy.context import PlanTier, TenantContext
from tests.providers._decision_fakes import RecordingController, ScriptedProvider

_CTX = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_AGENTS = [
    {"agent_id": "a1", "name": "Billing", "goal_template": "invoices"},
    {"agent_id": "a2", "name": "Support", "goal_template": "tickets"},
]


@pytest.fixture(autouse=True)
def _platform() -> Any:
    saved = gc._platform_services
    yield
    gc.set_platform_cost_services(saved)


async def test_llm_scoring_is_charged_to_the_tenant() -> None:
    ctrl = RecordingController()
    gc.set_platform_cost_services(lambda: (ctrl, None))
    provider = ScriptedProvider('{"best_agent_id": "a1", "confidence": 0.9, "reasoning": "x"}')
    scores = await AgentRouter(agent_store=None)._score_by_llm("pay invoice", _AGENTS, provider, tenant_ctx=_CTX)
    assert {s.agent_id for s in scores} == {"a1", "a2"}
    assert [t for _, t in ctrl.recorded] == ["t1"]


async def test_hung_or_over_budget_llm_scoring_falls_back_to_no_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTVERSE_DECISION_CALL_TIMEOUT_SECONDS", "0.05")
    router = AgentRouter(agent_store=None)
    hung = await asyncio.wait_for(
        router._score_by_llm("goal", _AGENTS, ScriptedProvider(hang=True), tenant_ctx=_CTX),
        timeout=5,
    )
    gc.set_platform_cost_services(lambda: (RecordingController(remaining=False), None))
    provider = ScriptedProvider('{"best_agent_id": "a1", "confidence": 0.9}')
    refused = await router._score_by_llm("goal", _AGENTS, provider, tenant_ctx=_CTX)
    assert hung == [] and refused == [] and provider.requests == []
