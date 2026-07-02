"""Tests for inline eval trigger: POST /goals/{id}/eval."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.goals import router as goals_router
from app.tenancy.context import PlanTier, TenantContext
from app.intelligence.eval import EvalScorecard

_TENANT = TenantContext(
    tenant_id="eval-tenant", plan=PlanTier.PROFESSIONAL, api_key_id="k"
)


def _make_app(svc):
    app = FastAPI()

    async def resolve(req, call_next):
        req.state.tenant = _TENANT
        return await call_next(req)

    app.add_middleware(BaseHTTPMiddleware, dispatch=resolve)
    app.include_router(goals_router)
    app.state.goal_service = svc
    return TestClient(app)


def test_post_eval_triggers_fresh_evaluation_and_returns_scorecard():
    svc = MagicMock()
    svc.run_eval = AsyncMock(return_value={
        "goal_id": "g-eval-1",
        "status": "evaluated",
        "scores": {
            "task_completion": 1.0,
            "efficiency": 0.8,
            "accuracy": 0.9,
            "safety": 1.0,
            "coherence": 0.75,
            "sla": 0.95,
            "tool_relevance": 0.88,
        },
        "average_score": 0.91,
        "passed": True,
        "iterations": 2,
    })
    client = _make_app(svc)
    resp = client.post("/goals/g-eval-1/eval")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "evaluated"
    assert body["scores"]["task_completion"] == 1.0
    assert body["scores"]["tool_relevance"] == 0.88
    assert body["passed"] is True
    svc.run_eval.assert_awaited_once_with(goal_id="g-eval-1", tenant_ctx=_TENANT)


def test_post_eval_returns_404_when_goal_not_found():
    svc = MagicMock()
    from app.core.errors import NotFoundError
    svc.run_eval = AsyncMock(side_effect=NotFoundError("Goal not found"))
    client = _make_app(svc)
    resp = client.post("/goals/nonexistent/eval")
    assert resp.status_code == 404
