"""Tests for /insights endpoints."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.insights import router as insights_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-ins", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "ak_test_insights123"
_HEADERS = {"X-API-Key": _VALID_KEY}


def _make_app() -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(insights_router)
    return app


def test_estimate_returns_defaults_without_goal_service() -> None:
    client = TestClient(_make_app())
    resp = client.post("/insights/estimate", json={"goal": "Deploy my app"}, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "estimated_cost_usd" in data
    assert "success_probability" in data
    assert "confidence" in data


def test_estimate_requires_auth() -> None:
    client = TestClient(_make_app())
    resp = client.post("/insights/estimate", json={"goal": "test"})
    assert resp.status_code == 401


def test_graph_returns_empty_without_events() -> None:
    app = _make_app()
    mock_svc = AsyncMock()
    mock_svc.get_event_log.return_value = []
    mock_svc.get_goal.return_value = None
    app.state.goal_service = mock_svc
    client = TestClient(app)
    resp = client.get("/insights/graph/goal-123", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "nodes" in data
    assert "edges" in data


def test_graph_returns_404_without_goal_service() -> None:
    client = TestClient(_make_app())
    resp = client.get("/insights/graph/goal-123", headers=_HEADERS)
    assert resp.status_code == 503


def test_analysis_returns_404_for_unknown_goal() -> None:
    app = _make_app()
    mock_svc = AsyncMock()
    mock_svc.get_goal.return_value = None
    app.state.goal_service = mock_svc
    client = TestClient(app)
    resp = client.get("/insights/analysis/ghost-id", headers=_HEADERS)
    assert resp.status_code == 404


def test_analysis_returns_heuristic_suggestions() -> None:
    app = _make_app()
    mock_svc = AsyncMock()
    mock_svc.get_goal.return_value = {
        "goal": "Call Jira API", "status": "failed",
        "verification_feedback": "rate limit exceeded 429",
        "steps": [], "iterations": 3, "cost_usd": 0.02,
    }
    app.state.goal_service = mock_svc
    client = TestClient(app)
    resp = client.get("/insights/analysis/g1", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["suggestions"]) > 0


def test_nl_query_parses_today() -> None:
    app = _make_app()
    mock_svc = AsyncMock()
    mock_svc.list_goals.return_value = []
    app.state.goal_service = mock_svc
    client = TestClient(app)
    resp = client.post(
        "/insights/query",
        json={"query": "show me goals that failed today", "entity": "goals"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["query_parsed"]["days"] == 1
    assert data["query_parsed"]["status_filter"] == "failed"


def test_agent_health_returns_defaults_without_db() -> None:
    client = TestClient(_make_app())
    resp = client.get("/insights/agent-health/agent-1", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "health" in data
    assert "success_rate" in data["health"]


def test_benchmarks_returns_platform_data() -> None:
    client = TestClient(_make_app())
    resp = client.get("/insights/benchmarks", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "platform_avg_success_rate" in data
    assert "percentile_bands" in data
