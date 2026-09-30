"""Debate / supervisor submission modes charge their LLM calls and check the budget first.

Regression: ``POST /goals`` with ``workflow_mode`` ``debate`` or ``supervisor``
ran the debate orchestrator / supervisor decomposition inside the HTTP request
on the raw platform provider — uncharged, with no circuit breaker or timeout,
and BEFORE the goal-submission budget preflight, so a tenant already over its
daily budget still triggered (unbilled) LLM calls.

Now the budget preflight runs first (429 when exhausted, no LLM call) and the
providers handed to those components route every call through
``app.providers.guarded_completion`` (breaker + timeout + tenant charge).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.goals import router as goals_router
from app.core.errors import PlatformError
from app.providers.guarded_completion import (
    DecisionBudgetExceededError,
    GuardedDecisionProvider,
    set_platform_cost_services,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.limits import PlanLimitExceededError
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-guard", plan=PlanTier.PROFESSIONAL, api_key_id="kid-g")
_KEY = "ak_test_guarded"


def _app(svc: Any, provider: Any) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)

    @app.exception_handler(PlatformError)
    async def _platform(_: Request, exc: PlatformError) -> JSONResponse:
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)

    app.include_router(goals_router)
    app.state.goal_service = svc
    app.state._app_provider = provider
    return app


def _svc(*, budget_ok: bool) -> Any:
    svc = AsyncMock()
    if budget_ok:
        svc._check_budget_preflight = AsyncMock(return_value=None)
    else:
        svc._check_budget_preflight = AsyncMock(
            side_effect=PlanLimitExceededError("Daily cost budget exhausted")
        )
    svc.submit_goal.return_value = {"id": "g", "goal_id": "g", "status": "planning", "goal": "x"}
    return svc


@pytest.mark.parametrize("mode", ["debate", "supervisor"])
def test_exhausted_budget_is_refused_before_any_llm_call(mode: str) -> None:
    provider = AsyncMock()
    client = TestClient(_app(_svc(budget_ok=False), provider), raise_server_exceptions=False)

    resp = client.post(
        "/goals",
        json={"goal": "Compare three caching designs", "workflow_mode": mode},
        headers={"X-API-Key": _KEY},
    )

    assert resp.status_code == 429, resp.text
    provider.complete.assert_not_awaited()


def test_debate_runs_on_a_guarded_provider() -> None:
    captured: dict[str, Any] = {}
    raw = MagicMock()

    class _Orchestrator:
        def __init__(self, *, provider: Any, rounds: int) -> None:
            captured["provider"] = provider

        async def run(self, goal: str) -> Any:
            return MagicMock(winning_proposal="A", consensus_level=0.9, winning_agent="a1")

    svc = _svc(budget_ok=True)
    with patch("app.agent.debate.DebateOrchestrator", _Orchestrator):
        client = TestClient(_app(svc, raw), raise_server_exceptions=False)
        resp = client.post(
            "/goals",
            json={"goal": "Pick a database", "workflow_mode": "debate"},
            headers={"X-API-Key": _KEY},
        )

    assert resp.status_code == 202, resp.text
    svc._check_budget_preflight.assert_awaited_once()
    provider = captured["provider"]
    assert isinstance(provider, GuardedDecisionProvider)
    assert provider.inner is raw
    assert provider._tenant_ctx.tenant_id == _CTX.tenant_id


def test_supervisor_makes_no_llm_call_in_the_request() -> None:
    """CORE-07: supervisor mode submits a parent goal whose graph decomposes it
    (with the goal's own charged planner); the request itself calls no LLM."""
    raw = AsyncMock()
    ran = MagicMock()

    svc = _svc(budget_ok=True)
    with patch("app.agent.supervisor.SupervisorAgent", ran):
        client = TestClient(_app(svc, raw), raise_server_exceptions=False)
        resp = client.post(
            "/goals",
            json={"goal": "Research and summarise", "workflow_mode": "supervisor"},
            headers={"X-API-Key": _KEY},
        )

    assert resp.status_code == 202, resp.text
    svc._check_budget_preflight.assert_awaited_once()
    ran.assert_not_called()
    raw.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_guarded_provider_refuses_a_tenant_over_budget() -> None:
    controller = MagicMock()
    controller.ahas_remaining_budget = AsyncMock(return_value=False)
    set_platform_cost_services(lambda: (controller, None))
    inner = AsyncMock()
    try:
        guarded = GuardedDecisionProvider(inner, role="debate", tenant_ctx=_CTX)
        with pytest.raises(DecisionBudgetExceededError):
            await guarded.complete(MagicMock(model="m"))
    finally:
        set_platform_cost_services(None)
    inner.complete.assert_not_awaited()
