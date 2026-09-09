"""Regression: the mission-execute path must use the REQUEST's wired app.state
(DB/Redis-backed GoalService), not the module-level app.main.app singleton whose
state carries unwired in-memory fallbacks."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.org.router import get_org_service, router

_SENTINEL_GOAL_SERVICE = object()  # identity marker for "the wired goal service"


def _make_app(spy: Any) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_tenant(request: Any, call_next: Any) -> Any:
        t = MagicMock()
        t.tenant_id = "t-mission"
        request.state.tenant = t
        return await call_next(request)

    app.include_router(router)
    # The lifespan-wired goal service lives on app.state.
    app.state.goal_service = _SENTINEL_GOAL_SERVICE
    app.dependency_overrides[get_org_service] = lambda: spy
    return app


@pytest.mark.asyncio
async def test_mission_execute_threads_request_app_state() -> None:
    captured: dict[str, Any] = {}

    class SpyService:
        async def create_mission_and_execute(self, **kwargs: Any):
            captured.update(kwargs)
            mission = SimpleNamespace(id="m1", title=kwargs["title"], status="active")
            return mission, {"goal_id": "g1", "topology": "sequential"}

    app = _make_app(SpyService())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/v1/org/org-1/missions/execute", json={"title": "Ship it"})

    assert resp.status_code in (200, 201), resp.text
    # The endpoint threaded the REQUEST's app.state (carrying the wired goal
    # service), not the module singleton's state.
    assert "app_state" in captured, "app_state was not threaded into the service call"
    assert getattr(captured["app_state"], "goal_service", None) is _SENTINEL_GOAL_SERVICE
    assert resp.json()["goal_id"] == "g1"
