"""Static workflow routes must not be shadowed by ``GET /workflows/{workflow_id}``.

``/templates``, ``/analytics/summary`` and ``/marketplace`` used to be declared
after ``/{workflow_id}``, so Starlette matched them as a workflow id: the
templates list, analytics summary and marketplace all returned a workflow
lookup (404 in production) and their handlers never ran.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


def _client(service: AsyncMock) -> TestClient:
    from fastapi import FastAPI, Request

    from app.tenancy.context import PlanLimits, PlanTier, TenantContext
    from app.workflow.router import router

    app = FastAPI()

    class FakeTenant(TenantContext):
        def __init__(self) -> None:
            pass

        tenant_id = "test-tenant"
        plan = PlanTier.FREE
        api_key = "test-key"
        api_key_id = "key-1"
        limits = PlanLimits(60, 25, 3, 2, 1, 3600)

    @app.middleware("http")
    async def inject_state(request: Request, call_next: Any) -> Any:
        request.app.state.workflow_service = service
        request.app.state.workflow_runner = None
        request.app.state.nl_trigger_resolver = None
        request.app.state.tenant_context = FakeTenant()
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def svc() -> AsyncMock:
    s = AsyncMock()
    # A workflow lookup never finds anything — if a static path is routed to
    # GET /{workflow_id} the response is a 404 "Workflow not found".
    s.get.return_value = None
    s.list_templates.return_value = ([{"slug": "kyc"}], 1)
    s.marketplace_list.return_value = ([{"slug": "mkt"}], 1)
    s.analytics_summary.return_value = {"total_runs": 3}
    return s


def test_templates_list_not_shadowed(svc: AsyncMock) -> None:
    resp = _client(svc).get("/api/v1/workflows/templates")
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == [{"slug": "kyc"}]
    svc.list_templates.assert_awaited_once()
    svc.get.assert_not_awaited()


def test_marketplace_not_shadowed(svc: AsyncMock) -> None:
    resp = _client(svc).get("/api/v1/workflows/marketplace")
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == [{"slug": "mkt"}]
    svc.marketplace_list.assert_awaited_once()
    svc.get.assert_not_awaited()


def test_analytics_summary_not_shadowed(svc: AsyncMock) -> None:
    resp = _client(svc).get("/api/v1/workflows/analytics/summary")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"total_runs": 3}
    svc.analytics_summary.assert_awaited_once()


def test_dynamic_workflow_route_still_works(svc: AsyncMock) -> None:
    resp = _client(svc).get("/api/v1/workflows/wf-404")
    assert resp.status_code == 404
    svc.get.assert_awaited_once()
