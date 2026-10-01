"""ORG-36: a civilization goal submit answers "accepted" only with the real goal id.

The orchestrator used to swallow every GoalService failure (and a missing goal
service) and still return ``accepted`` with a synthetic ``civ_<uuid>`` id, so quota
rejections and outages looked like accepted goals that then 404.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.civilization import router
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-civ", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _Result:
    def fetchone(self) -> Any:
        return ({"total_budget_usd": 10.0}, "active")


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        return _Result()


def _db() -> _Session:
    return _Session()


@asynccontextmanager
async def _no_rls(session: Any, tenant_id: str) -> Any:
    yield session


async def _route_single(*a: Any, **kw: Any) -> dict:
    return {"mode": "single_agent", "agent_id": "a1", "confidence": 0.9}


def _submit(goal_service: Any) -> Any:
    app = FastAPI()

    @app.middleware("http")
    async def _t(request: Any, call_next: Any) -> Any:
        request.state.tenant = CTX
        return await call_next(request)

    app.include_router(router)
    app.state.goal_service = goal_service
    with (
        patch("app.api.civilization._require_feature_enabled", lambda r: None),
        patch("app.api.civilization._get_db", lambda r: _db),
        patch("app.api.civilization._rls_ctx", _no_rls),
        patch("app.civilization.society.Society.route_goal", _route_single),
        patch("app.civilization.orchestrator.emit_event", AsyncMock(return_value="e")),
        patch("app.civilization.blackboard.Blackboard.query", AsyncMock(return_value=[])),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        return client.post("/civilizations/civ-1/goals", json={"goal": "Analyze churn"})


def test_goal_service_outage_is_503_not_accepted() -> None:
    gs = AsyncMock()
    gs.submit_goal = AsyncMock(side_effect=RuntimeError("asyncpg: connection refused"))
    r = _submit(gs)
    assert r.status_code == 503, r.text
    assert "accepted" not in r.text
    assert "asyncpg" not in r.text


def test_missing_goal_service_is_503() -> None:
    r = _submit(None)
    assert r.status_code == 503, r.text
    assert "accepted" not in r.text


def test_quota_rejection_keeps_its_status() -> None:
    gs = AsyncMock()
    gs.submit_goal = AsyncMock(side_effect=HTTPException(429, "Daily goal limit reached"))
    r = _submit(gs)
    assert r.status_code == 429, r.text


def test_accepted_carries_the_real_goal_id() -> None:
    gs = AsyncMock()
    gs.submit_goal = AsyncMock(return_value={"goal_id": "goal-real-1", "status": "pending"})
    r = _submit(gs)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "accepted"
    assert r.json()["goal_id"] == "goal-real-1"
