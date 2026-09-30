"""PROV-01: the remaining direct LLM calls go through ``complete_decision``.

Covers the mechanics the migration relies on (guarded wrappers are not charged
twice, the request/job tenant scope, the 429 mapping) and representative
tenant-reachable call sites: each is refused before any provider call when the
tenant is out of budget, and charges the tenant otherwise.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.providers import guarded_completion as gc
from app.providers.base import CompletionRequest, CompletionResponse, Message
from app.providers.guarded_completion import (
    DecisionBudgetExceededError,
    GuardedDecisionProvider,
    complete_decision,
    tenant_charge_scope,
)
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t-prov01", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _req() -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="hi")], model="gpt-4o-mini")


class _Provider:
    def __init__(self, reply: str = "ok", *, hang: bool = False) -> None:
        self._default_model = "gpt-4o-mini"
        self.reply, self.hang = reply, hang
        self.calls = 0

    async def complete(self, request: Any) -> CompletionResponse:
        self.calls += 1
        if self.hang:
            await asyncio.sleep(30)
        return CompletionResponse(
            content=self.reply, model="gpt-4o-mini", input_tokens=100, output_tokens=10
        )


@dataclass
class _Controller:
    remaining: bool = True
    allow: bool = True
    recorded: list[tuple[str, str]] = field(default_factory=list)

    async def check_and_record(self, *, goal_id: str, cost_usd: float, tenant_ctx: Any) -> bool:
        self.recorded.append((goal_id, tenant_ctx.tenant_id))
        return self.allow

    async def ahas_remaining_budget(self, *, tenant_ctx: Any) -> bool:
        return self.remaining


@pytest.fixture
def controller() -> Any:
    saved = gc._platform_services
    ctrl = _Controller()
    gc.set_platform_cost_services(lambda: (ctrl, None))
    yield ctrl
    gc.set_platform_cost_services(saved)


# ── mechanics ─────────────────────────────────────────────────────────────────


async def test_guarded_wrapper_is_not_charged_twice(controller: _Controller) -> None:
    inner = _Provider()
    wrapped = GuardedDecisionProvider(inner, role="debate", tenant_ctx=_CTX)
    await complete_decision(wrapped, _req(), role="debate_propose", tenant_ctx=_CTX)
    assert inner.calls == 1
    assert len(controller.recorded) == 1


async def test_charging_provider_gets_breaker_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent.nodes.llm_cost import ChargingProvider

    monkeypatch.setenv("AGENTVERSE_LLM_CALL_TIMEOUT_SECONDS", "0.05")
    graph = SimpleNamespace(_cost_controller=None, _cost_tracker=None, _state_lock=None)
    state = SimpleNamespace(goal_id="g", context={})
    wrapped = ChargingProvider(
        _Provider(hang=True), graph=graph, role="self_consistency", agent_state=state,
        tenant_ctx=_CTX,
    )
    with pytest.raises(Exception):
        await asyncio.wait_for(wrapped.complete(_req()), timeout=5)


async def test_tenant_scope_charges_calls_without_an_explicit_tenant(
    controller: _Controller,
) -> None:
    with tenant_charge_scope(_CTX):
        await complete_decision(_Provider(), _req(), role="x")
    assert [tid for _g, tid in controller.recorded] == ["t-prov01"]


async def test_tenant_scope_refusal_blocks_the_call(controller: _Controller) -> None:
    controller.remaining = False
    provider = _Provider()
    with tenant_charge_scope(_CTX), pytest.raises(DecisionBudgetExceededError):
        await complete_decision(provider, _req(), role="x")
    assert provider.calls == 0


async def test_tenant_middleware_enters_the_charge_scope() -> None:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from app.tenancy.middleware import TenantMiddleware

    async def endpoint(request: Request) -> JSONResponse:
        scoped = gc._tenant(None, None)
        return JSONResponse({"tenant": getattr(scoped, "tenant_id", None)})

    async def resolver(raw_key: str) -> TenantContext | None:
        return _CTX if raw_key == "good-key" else None

    app = Starlette(routes=[Route("/api/v1/probe", endpoint)])
    app.add_middleware(TenantMiddleware, key_resolver=resolver)
    with TestClient(app) as client:
        resp = client.get("/api/v1/probe", headers={"X-API-Key": "good-key"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"tenant": "t-prov01"}


async def test_budget_refusal_maps_to_429_with_a_stable_code() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.main import _register_error_handlers

    app = FastAPI()
    _register_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise DecisionBudgetExceededError("tenant daily LLM budget exhausted")

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/boom")
    assert resp.status_code == 429
    assert resp.json()["code"] == "llm_budget_exhausted"


# ── representative call sites ─────────────────────────────────────────────────


def _results(n: int = 3) -> list[Any]:
    from app.rag.engine import RetrievalResult

    return [
        RetrievalResult(
            chunk_id=f"c{i}", content=f"passage {i}", score=1.0 - i / 10, source_metadata={}
        )
        for i in range(n)
    ]


async def test_rag_rerank_charges_and_is_refused_without_budget(
    controller: _Controller,
) -> None:
    from app.rag.engine import rerank_results

    provider = _Provider("[0.1, 0.9, 0.5]")
    with tenant_charge_scope(_CTX):
        out = await rerank_results(_results(), "q", provider=provider)
    assert [r.chunk_id for r in out][0] == "c1"
    assert controller.recorded and controller.recorded[0][1] == "t-prov01"

    controller.remaining = False
    blocked = _Provider("[0.1, 0.9, 0.5]")
    with tenant_charge_scope(_CTX):
        await rerank_results(_results(), "q", provider=blocked)  # falls back to RRF order
    assert blocked.calls == 0


async def test_skill_test_endpoint_surfaces_budget_refusal(controller: _Controller) -> None:
    from app.agent.skill_selector import PLATFORM_SKILLS
    from app.api.skills import SkillTestRequest, run_skill_test

    controller.remaining = False
    provider = _Provider()
    request = MagicMock()
    request.state = SimpleNamespace(tenant=_CTX)
    request.app.state = SimpleNamespace(db_session_factory=None, llm_provider=provider)
    with pytest.raises(DecisionBudgetExceededError):
        await run_skill_test(
            PLATFORM_SKILLS[0]["id"], SkillTestRequest(input="hello"), request
        )
    assert provider.calls == 0


async def test_goal_tree_decomposition_charges_the_parent_goal_tenant(
    controller: _Controller,
) -> None:
    from app.agent.goal_tree import decompose_goal

    provider = _Provider('{"decompose": false}')
    result = await decompose_goal("do x", provider, _CTX, "parent-goal")
    assert result.should_decompose is False
    assert controller.recorded == [("parent-goal", "t-prov01")]


async def test_workflow_decision_node_charges_the_workflow_tenant(
    controller: _Controller,
) -> None:
    from app.agent.workflow_nodes import execute_decision_node

    provider = _Provider("approve")
    edge = await execute_decision_node(
        {"condition": "llm", "options": ["approve", "reject"]},
        {"goal": "g"},
        llm_provider=provider,
        tenant_ctx=_CTX,
    )
    assert edge == "approve"
    assert [tid for _g, tid in controller.recorded] == ["t-prov01"]
