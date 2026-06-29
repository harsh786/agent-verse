"""Comprehensive tests for /analytics API endpoints — targets 25% → 65%+ coverage."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.analytics import router as analytics_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-analytics", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_analytics_comp"


def _make_app(goal_service: Any = None, aggregator: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(analytics_router)
    if goal_service:
        app.state.goal_service = goal_service
    return app


def _make_mock_aggregator() -> Any:
    from app.analytics.aggregator import GoalMetrics, ToolMetrics, AgentMetrics

    agg = MagicMock()

    metrics = MagicMock(spec=GoalMetrics)
    metrics.total = 10
    metrics.completed = 8
    metrics.failed = 1
    metrics.cancelled = 1
    metrics.success_rate = 0.8
    metrics.avg_duration_s = 30.5
    metrics.avg_cost_usd = 0.5
    metrics.total_cost_usd = 5.0
    agg.goal_metrics = AsyncMock(return_value=metrics)

    tool = MagicMock(spec=ToolMetrics)
    tool.tool_name = "github.read"
    tool.call_count = 50
    tool.failure_count = 2
    tool.failure_rate = 0.04
    tool.avg_latency_ms = 120.0
    agg.tool_metrics = MagicMock(return_value=[tool])

    trend = {"date": "2024-01-01", "cost_usd": 1.5}
    agg.cost_trends = MagicMock(return_value=[trend])

    agent = MagicMock(spec=AgentMetrics)
    agent.agent_id = "agent-1"
    agent.goal_count = 5
    agent.success_rate = 1.0
    agent.avg_eval_score = 0.9
    agent.avg_cost_usd = 0.4
    agg.agent_metrics = MagicMock(return_value=[agent])

    return agg


# ---------------------------------------------------------------------------
# goal_analytics
# ---------------------------------------------------------------------------

def test_goal_analytics_basic(monkeypatch) -> None:
    agg = _make_mock_aggregator()
    monkeypatch.setattr("app.analytics.aggregator.GoalAnalyticsAggregator", lambda **kw: agg)

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/analytics/goals", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 10
    assert body["completed"] == 8
    assert body["success_rate"] == 0.8


def test_goal_analytics_days_param(monkeypatch) -> None:
    agg = _make_mock_aggregator()
    monkeypatch.setattr("app.analytics.aggregator.GoalAnalyticsAggregator", lambda **kw: agg)

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/analytics/goals?days=7", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    agg.goal_metrics.assert_called_with(
        tenant_id=_CTX.tenant_id,
        days=7,
        agent_id=None,
    )


def test_goal_analytics_days_out_of_range(monkeypatch) -> None:
    agg = _make_mock_aggregator()
    monkeypatch.setattr("app.analytics.aggregator.GoalAnalyticsAggregator", lambda **kw: agg)

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/analytics/goals?days=400", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 422


def test_goal_analytics_with_agent_filter(monkeypatch) -> None:
    agg = _make_mock_aggregator()
    monkeypatch.setattr("app.analytics.aggregator.GoalAnalyticsAggregator", lambda **kw: agg)

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get(
        "/analytics/goals?agent_id=agent-1",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    agg.goal_metrics.assert_called_with(
        tenant_id=_CTX.tenant_id,
        days=30,
        agent_id="agent-1",
    )


# ---------------------------------------------------------------------------
# tool_analytics
# ---------------------------------------------------------------------------

def test_tool_analytics(monkeypatch) -> None:
    agg = _make_mock_aggregator()
    monkeypatch.setattr("app.analytics.aggregator.GoalAnalyticsAggregator", lambda **kw: agg)

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/analytics/tools", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["period_days"] == 30
    assert len(body["tools"]) == 1
    tool = body["tools"][0]
    assert tool["name"] == "github.read"
    assert tool["call_count"] == 50
    assert tool["failure_rate"] == pytest.approx(0.04)


def test_tool_analytics_empty(monkeypatch) -> None:
    agg = _make_mock_aggregator()
    agg.tool_metrics = MagicMock(return_value=[])
    monkeypatch.setattr("app.analytics.aggregator.GoalAnalyticsAggregator", lambda **kw: agg)

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/analytics/tools", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["tools"] == []


# ---------------------------------------------------------------------------
# cost_analytics
# ---------------------------------------------------------------------------

def test_cost_analytics(monkeypatch) -> None:
    agg = _make_mock_aggregator()
    monkeypatch.setattr("app.analytics.aggregator.GoalAnalyticsAggregator", lambda **kw: agg)

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/analytics/costs", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["period_days"] == 30
    assert body["total_cost_usd"] == pytest.approx(1.5)
    assert "trends" in body


def test_cost_analytics_with_goal_service(monkeypatch) -> None:
    agg = _make_mock_aggregator()
    monkeypatch.setattr("app.analytics.aggregator.GoalAnalyticsAggregator", lambda **kw: agg)

    goal_svc = AsyncMock()
    goal_svc.get_metrics = AsyncMock(return_value={"cost_today_usd": 0.75})
    client = TestClient(_make_app(goal_service=goal_svc), raise_server_exceptions=False)
    resp = client.get("/analytics/costs", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["cost_today_usd"] == pytest.approx(0.75)


def test_cost_analytics_week_bucket(monkeypatch) -> None:
    agg = _make_mock_aggregator()
    monkeypatch.setattr("app.analytics.aggregator.GoalAnalyticsAggregator", lambda **kw: agg)

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/analytics/costs?bucket=week", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["bucket"] == "week"


def test_cost_analytics_invalid_bucket(monkeypatch) -> None:
    agg = _make_mock_aggregator()
    monkeypatch.setattr("app.analytics.aggregator.GoalAnalyticsAggregator", lambda **kw: agg)

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/analytics/costs?bucket=month", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# agent_analytics
# ---------------------------------------------------------------------------

def test_agent_analytics(monkeypatch) -> None:
    agg = _make_mock_aggregator()
    monkeypatch.setattr("app.analytics.aggregator.GoalAnalyticsAggregator", lambda **kw: agg)

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/analytics/agents", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["period_days"] == 30
    assert len(body["agents"]) == 1
    assert body["agents"][0]["agent_id"] == "agent-1"


# ---------------------------------------------------------------------------
# get_spans
# ---------------------------------------------------------------------------

def test_get_spans(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.observability.tracing.get_recent_spans",
        lambda limit: [{"span_id": "s1", "name": "test"}],
    )
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/analytics/observability/spans", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# eval_analytics
# ---------------------------------------------------------------------------

def test_eval_analytics_no_db() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/analytics/evals", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["pass_rate"] == 0.0
    assert "avg_scores" in body


def test_eval_analytics_requires_tenant() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    # No X-API-Key header
    resp = client.get("/analytics/evals")
    assert resp.status_code == 401
