"""PROV-04: LLM spend is recorded under cost metric scope 'llm', not 'tool'."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.governance import cost as cost_mod
from app.governance.cost import CostController, RedisCostController, llm_spend
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t-scope", plan=PlanTier.ENTERPRISE, api_key_id="k")


@pytest.fixture
def scopes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(cost_mod, "record_cost_usd", lambda scope, amount: seen.append(scope))
    return seen


async def test_tool_spend_keeps_scope_tool(scopes: list[str]) -> None:
    assert await CostController().check_and_record(goal_id="g", cost_usd=0.01, tenant_ctx=_CTX)
    assert scopes == ["tool"]


async def test_llm_spend_is_scope_llm(scopes: list[str]) -> None:
    ok = await llm_spend(CostController().check_and_record(goal_id="g", cost_usd=0.01, tenant_ctx=_CTX))
    assert ok and scopes == ["llm"]


async def test_redis_controller_llm_spend_is_scope_llm(scopes: list[str]) -> None:
    from app.main import _FakeRedis

    ctrl = RedisCostController(redis=_FakeRedis())
    assert await llm_spend(ctrl.check_and_record(goal_id="g", cost_usd=0.01, tenant_ctx=_CTX))
    assert scopes == ["llm"]


async def test_charge_llm_call_records_scope_llm(scopes: list[str]) -> None:
    from app.agent.nodes.llm_cost import charge_llm_call

    graph = SimpleNamespace(_cost_controller=CostController(), _cost_tracker=None, _state_lock=None)
    state = SimpleNamespace(goal_id="g", context={})
    resp: Any = SimpleNamespace(model="gpt-4o-mini", input_tokens=100, output_tokens=10, usage=None)
    await charge_llm_call(
        graph, resp=resp, role="planner", model="gpt-4o-mini", agent_state=state, tenant_ctx=_CTX
    )
    assert scopes == ["llm"]
