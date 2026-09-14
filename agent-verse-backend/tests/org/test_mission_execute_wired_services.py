"""Regression: the mission-execute path must use the REQUEST's wired app.state
(DB/Redis-backed GoalService), not the module-level app.main.app singleton whose
state carries unwired in-memory fallbacks.

The endpoint constructs OrgService inline against a DB session and, when no Celery
worker is wired (the goal service has no task queue), dispatches the mission
INLINE via form_team_and_dispatch — threading the request's app.state through so
the goal runs on the wired services.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.org.router import get_org_service, router

_SENTINEL_GOAL_SERVICE = object()  # identity marker for "the wired goal service"


class _FakeTx:
    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


class _FakeSession:
    """No-op async session: the endpoint only opens it, begins a tx, and sets the
    RLS GUC (SELECT set_config) — the real work is on the (patched) OrgService."""

    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False

    def begin(self) -> _FakeTx:
        return _FakeTx()

    async def execute(self, *_a: Any, **_k: Any) -> None:
        return None

    async def flush(self) -> None:
        return None


class _FakeSessionFactory:
    def __call__(self) -> _FakeSession:
        return _FakeSession()


def _make_app(spy: Any) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_tenant(request: Any, call_next: Any) -> Any:
        t = MagicMock()
        t.tenant_id = "t-mission"
        request.state.tenant = t
        return await call_next(request)

    app.include_router(router)
    # The lifespan-wired goal service lives on app.state (no task queue → the
    # endpoint dispatches inline rather than enqueuing to a worker).
    app.state.goal_service = _SENTINEL_GOAL_SERVICE
    app.dependency_overrides[get_org_service] = lambda: spy
    return app


@pytest.mark.asyncio
async def test_mission_execute_threads_request_app_state(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class SpyService:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def create_mission(self, **kwargs: Any) -> Any:
            return SimpleNamespace(id="m1", title=kwargs.get("title", ""), status="planned")

        async def update_mission_status(self, *_a: Any, **_k: Any) -> None:
            return None

        async def get_mission(self, *_a: Any, **_k: Any) -> Any:
            return SimpleNamespace(id="m1", title="Ship it", status="planned")

        async def form_team_and_dispatch(self, **kwargs: Any) -> dict[str, Any]:
            # The inline (no-worker) dispatch path threads the REQUEST's app_state.
            captured.update(kwargs)
            return {"goal_id": "g1", "team_id": "t1", "topology": "sequential"}

    spy = SpyService()
    # app.org.__init__ re-exports `router`, which shadows the submodule name, so
    # reach the real router module object via sys.modules to patch its OrgService.
    import sys

    _org_router_mod = sys.modules["app.org.router"]
    monkeypatch.setattr(_org_router_mod, "OrgService", lambda **_k: spy)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _FakeSessionFactory())

    app = _make_app(spy)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/v1/org/org-1/missions/execute", json={"title": "Ship it"})

    assert resp.status_code in (200, 201), resp.text
    # The endpoint threaded the REQUEST's app.state (carrying the wired goal
    # service) into the inline dispatch, not the module singleton's state.
    assert "app_state" in captured, "app_state was not threaded into the dispatch call"
    assert getattr(captured["app_state"], "goal_service", None) is _SENTINEL_GOAL_SERVICE
    assert resp.json()["goal_id"] == "g1"
