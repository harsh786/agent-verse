"""MEM-24: the AI-Ops judge and the meta-agent planner are charged and bounded.

Both called ``provider.complete()`` directly: no tenant budget charge, no token
ledger, no circuit breaker, and (for the judge) no timeout, so a hung provider
stalled a whole dataset run.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.evals.ai_ops_runner import judge_case
from app.intelligence.meta_agent import MetaAgentPlanner
from app.providers import guarded_completion as gc
from app.providers.guarded_completion import DecisionBudgetExceededError
from app.tenancy.context import PlanTier, TenantContext
from tests.providers._decision_fakes import RecordingController, ScriptedProvider

_CTX = TenantContext(tenant_id="t-judge", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_JUDGE = {"evaluation_dimensions": ["correctness"]}


@pytest.fixture(autouse=True)
def _platform() -> Any:
    saved = gc._platform_services
    yield
    gc.set_platform_cost_services(saved)


async def test_judge_charges_the_tenant() -> None:
    ctrl = RecordingController()
    gc.set_platform_cost_services(lambda: (ctrl, None))
    verdict = await judge_case(
        provider=ScriptedProvider('{"correctness": 0.9, "reasoning": "ok"}'),
        judge=_JUDGE, task_input="q", expected="a", actual="a", tenant_ctx=_CTX,
    )
    assert verdict["score"] == pytest.approx(0.9)
    assert [tid for _gid, tid in ctrl.recorded] == ["t-judge"]


async def test_hung_judge_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTVERSE_DECISION_CALL_TIMEOUT_SECONDS", "0.05")
    gc.set_platform_cost_services(lambda: (RecordingController(), None))
    verdict = await asyncio.wait_for(
        judge_case(
            provider=ScriptedProvider(hang=True),
            judge=_JUDGE, task_input="q", expected="a", actual="a", tenant_ctx=_CTX,
        ),
        timeout=5,
    )
    assert "error" in verdict


async def test_judge_refused_by_budget_spends_nothing() -> None:
    gc.set_platform_cost_services(lambda: (RecordingController(remaining=False), None))
    provider = ScriptedProvider('{"correctness": 1.0}')
    verdict = await judge_case(
        provider=provider, judge=_JUDGE, task_input="q", expected="a", actual="a",
        tenant_ctx=_CTX,
    )
    assert "budget" in verdict["error"]
    assert provider.requests == []


async def test_meta_agent_charges_the_tenant() -> None:
    ctrl = RecordingController()
    gc.set_platform_cost_services(lambda: (ctrl, None))
    planner = MetaAgentPlanner(ScriptedProvider('{"name": "Digest", "goal_template": "x"}'))
    config = await planner.plan(command="make a digest agent", tenant_ctx=_CTX)
    assert config.name == "Digest"
    assert [tid for _gid, tid in ctrl.recorded] == ["t-judge"]


async def test_meta_agent_budget_refusal_is_raised_not_hidden() -> None:
    gc.set_platform_cost_services(lambda: (RecordingController(remaining=False), None))
    provider = ScriptedProvider('{"name": "Digest"}')
    planner = MetaAgentPlanner(provider)
    with pytest.raises(DecisionBudgetExceededError):
        await planner.plan(command="make a digest agent", tenant_ctx=_CTX)
    assert provider.requests == []


def test_nl_agent_creation_over_budget_is_429() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.agents import AgentStore
    from app.api.agents import router as agents_router
    from app.tenancy.middleware import TenantMiddleware

    gc.set_platform_cost_services(lambda: (RecordingController(remaining=False), None))
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == "k-nl" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(agents_router)
    app.state.agent_store = AgentStore()
    app.state.meta_agent = MetaAgentPlanner(ScriptedProvider('{"name": "x"}'))
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/agents/create", json={"command": "a digest agent"}, headers={"X-API-Key": "k-nl"})
    assert r.status_code == 429, r.text
