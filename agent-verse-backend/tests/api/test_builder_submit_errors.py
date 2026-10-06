"""POST /builder/projects must not report client-attributable refusals as 503.

Regression for the schema-driven cross-tenant sweep
(tests/e2e_full/test_cross_tenant_sweep_e2e.py): on a free-plan tenant that
already had a goal running, ``GoalService.submit_goal`` raised
``PlanLimitExceededError`` (a 429 PlatformError) and the builder's blanket
``except Exception`` rewrote it to ``503 Could not start builder`` — telling a
tenant at its plan limit that the server was unavailable.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from app.api.builder import router
from app.core.errors import BudgetExceededError, PlatformError
from app.tenancy.limits import PlanLimitExceededError


class _GoalService:
    def __init__(self, exc: BaseException | None = None) -> None:
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    async def submit_goal(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return {"goal_id": "goal-123"}


def _app(goal_service: Any) -> FastAPI:
    app = FastAPI()
    app.state.goal_service = goal_service

    # Same shape as app.main._register_error_handlers: a PlatformError renders
    # with its own http_status.
    @app.exception_handler(PlatformError)
    async def _platform(_: Request, exc: PlatformError) -> JSONResponse:
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        request.state.tenant = SimpleNamespace(tenant_id="tenant-builder", plan="free")
        return await call_next(request)

    app.include_router(router)
    return app


async def _post(app: FastAPI) -> Any:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    ) as client:
        return await client.post("/builder/projects", json={"description": "a landing page"})


async def test_plan_limit_is_429_not_503() -> None:
    svc = _GoalService(PlanLimitExceededError("Concurrent goal limit (1) reached"))
    resp = await _post(_app(svc))
    assert resp.status_code == 429, resp.text
    assert resp.json()["error"]["code"] == "PLAN_LIMIT_EXCEEDED"


async def test_budget_exhausted_is_402_not_503() -> None:
    resp = await _post(_app(_GoalService(BudgetExceededError("Daily budget exhausted"))))
    assert resp.status_code == 402, resp.text


async def test_http_exception_from_submit_keeps_its_status() -> None:
    resp = await _post(_app(_GoalService(HTTPException(404, "Agent not found"))))
    assert resp.status_code == 404, resp.text


async def test_unexpected_failure_is_still_503(caplog: pytest.LogCaptureFixture) -> None:
    resp = await _post(_app(_GoalService(RuntimeError("redis down"))))
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Could not start builder: RuntimeError"
    # The real cause is logged server-side (it used to vanish without a trace).
    assert any("builder_goal_submit_failed" in r.getMessage() for r in caplog.records)


async def test_success_returns_project_with_goal_id() -> None:
    svc = _GoalService()
    resp = await _post(_app(svc))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["goal_id"] == "goal-123"
    # No live preview exists, so none is advertised; the build is a queued goal.
    assert "preview_url" not in body  # no live preview (a10-F229-01)
    assert body["status"] == "submitted"
    assert svc.calls[0]["execution_context"]["builder_project_id"] == body["project_id"]
