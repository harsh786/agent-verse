"""Comprehensive tests for app/api/insights.py — targets the 12% baseline."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.insights import router as insights_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="ins-t1", plan=PlanTier.PROFESSIONAL, api_key_id="ins-key")
_VALID_KEY = "ins-key"
_HEADERS = {"X-API-Key": _VALID_KEY}


def _make_app(goal_service: Any = None, embedder: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(insights_router)
    if goal_service is not None:
        app.state.goal_service = goal_service
    if embedder is not None:
        app.state.embedder = embedder
    return app


# ── Auth guard ────────────────────────────────────────────────────────────────

def test_estimate_requires_auth() -> None:
    client = TestClient(_make_app())
    resp = client.post("/insights/estimate", json={"goal": "test"})
    assert resp.status_code == 401


def test_graph_requires_auth() -> None:
    mock_svc = MagicMock()
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/insights/graph/g1")
    assert resp.status_code == 401


def test_benchmarks_requires_auth() -> None:
    client = TestClient(_make_app())
    resp = client.get("/insights/benchmarks")
    assert resp.status_code == 401


# ── POST /insights/estimate ───────────────────────────────────────────────────

def test_estimate_returns_defaults_without_goal_service() -> None:
    client = TestClient(_make_app())
    resp = client.post("/insights/estimate", json={"goal": "Deploy my app"}, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "estimated_cost_usd" in data
    assert "estimated_duration_s" in data
    assert "estimated_iterations" in data
    assert "success_probability" in data
    assert "confidence" in data
    assert data["confidence"] == "low"
    assert data["based_on"] == "platform_defaults"


def test_estimate_requires_non_empty_goal() -> None:
    client = TestClient(_make_app())
    resp = client.post("/insights/estimate", json={"goal": ""}, headers=_HEADERS)
    assert resp.status_code == 422


def test_estimate_with_agent_id() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/insights/estimate",
        json={"goal": "Do something", "agent_id": "my-agent"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200


def test_estimate_goal_too_long_returns_422() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/insights/estimate",
        json={"goal": "x" * 10_001},
        headers=_HEADERS,
    )
    assert resp.status_code == 422


# ── GET /insights/graph/{goal_id} ─────────────────────────────────────────────

def test_graph_no_goal_service_returns_503() -> None:
    client = TestClient(_make_app())
    resp = client.get("/insights/graph/g1", headers=_HEADERS)
    assert resp.status_code == 503


def test_graph_empty_events_returns_start_node() -> None:
    mock_svc = MagicMock()
    mock_svc.get_event_log = AsyncMock(return_value=[])
    mock_svc.get_goal = AsyncMock(return_value=None)
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/insights/graph/goal-x", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["goal_id"] == "goal-x"
    assert any(n["id"] == "start" for n in data["nodes"])
    assert data["stats"]["total_nodes"] >= 1


def test_graph_with_step_events() -> None:
    events = [
        {"type": "step_start", "payload": {"description": "Fetch data", "status": "complete"}},
        {"type": "step_complete", "payload": {"description": "Process data"}},
        {"type": "goal_complete", "payload": {}},
    ]
    mock_svc = MagicMock()
    mock_svc.get_event_log = AsyncMock(return_value=events)
    mock_svc.get_goal = AsyncMock(return_value=None)
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/insights/graph/goal-steps", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    # Should have start + 2 steps + end
    node_types = [n["type"] for n in data["nodes"]]
    assert "start" in node_types
    assert "step" in node_types
    assert "end" in node_types


def test_graph_with_tool_events() -> None:
    events = [
        {"type": "tool_call", "payload": {"tool_name": "search", "arguments": {"q": "test"}}},
        {"type": "tool_result", "payload": {"tool_name": "search", "result": "found"}},
    ]
    mock_svc = MagicMock()
    mock_svc.get_event_log = AsyncMock(return_value=events)
    mock_svc.get_goal = AsyncMock(return_value=None)
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/insights/graph/goal-tools", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    node_types = [n["type"] for n in data["nodes"]]
    assert "tool" in node_types
    assert data["stats"]["tool_calls"] >= 1


def test_graph_goal_failed_creates_end_node() -> None:
    events = [{"type": "goal_failed", "payload": {}}]
    mock_svc = MagicMock()
    mock_svc.get_event_log = AsyncMock(return_value=events)
    mock_svc.get_goal = AsyncMock(return_value=None)
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/insights/graph/goal-fail", headers=_HEADERS)
    assert resp.status_code == 200
    assert any(n["id"] == "end" for n in resp.json()["nodes"])


def test_graph_uses_goal_events_when_service_empty() -> None:
    """Fall back to goal record events when get_event_log returns []."""
    events_from_goal = [
        {"type": "step_start", "payload": {"description": "S1"}},
    ]
    mock_svc = MagicMock()
    mock_svc.get_event_log = AsyncMock(return_value=[])
    mock_svc.get_goal = AsyncMock(return_value={"events": events_from_goal})
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/insights/graph/goal-fallback", headers=_HEADERS)
    assert resp.status_code == 200


# ── GET /insights/analysis/{goal_id} ──────────────────────────────────────────

def test_analysis_no_goal_service_returns_503() -> None:
    client = TestClient(_make_app())
    resp = client.get("/insights/analysis/g1", headers=_HEADERS)
    assert resp.status_code == 503


def test_analysis_goal_not_found_returns_404() -> None:
    mock_svc = MagicMock()
    mock_svc.get_goal = AsyncMock(return_value=None)
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/insights/analysis/ghost", headers=_HEADERS)
    assert resp.status_code == 404


def test_analysis_goal_service_raises_returns_404() -> None:
    mock_svc = MagicMock()
    mock_svc.get_goal = AsyncMock(side_effect=RuntimeError("not found"))
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/insights/analysis/error-goal", headers=_HEADERS)
    assert resp.status_code == 404


def test_analysis_rate_limit_suggestion() -> None:
    mock_svc = MagicMock()
    mock_svc.get_goal = AsyncMock(return_value={
        "goal": "Call Jira API",
        "status": "failed",
        "verification_feedback": "rate limit exceeded 429",
        "steps": [],
        "iterations": 2,
        "cost_usd": 0.01,
    })
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/insights/analysis/g1", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert any("Rate limit" in s.get("action", "") for s in data["suggestions"])


def test_analysis_timeout_suggestion() -> None:
    mock_svc = MagicMock()
    mock_svc.get_goal = AsyncMock(return_value={
        "goal": "Long task",
        "status": "failed",
        "verification_feedback": "operation timed out after 5 minutes",
        "steps": [],
    })
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/insights/analysis/g2", headers=_HEADERS)
    assert resp.status_code == 200
    assert any("Timeout" in s.get("action", "") for s in resp.json()["suggestions"])


def test_analysis_permission_suggestion() -> None:
    mock_svc = MagicMock()
    mock_svc.get_goal = AsyncMock(return_value={
        "goal": "Write file",
        "status": "failed",
        "verification_feedback": "403 Forbidden — permission denied",
        "steps": [],
    })
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/insights/analysis/g3", headers=_HEADERS)
    assert resp.status_code == 200
    assert any("Permissions" in s.get("action", "") for s in resp.json()["suggestions"])


def test_analysis_not_found_suggestion() -> None:
    mock_svc = MagicMock()
    mock_svc.get_goal = AsyncMock(return_value={
        "goal": "Delete record",
        "status": "failed",
        "verification_feedback": "404 not found",
        "steps": [],
    })
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/insights/analysis/g4", headers=_HEADERS)
    assert resp.status_code == 200
    assert any("Missing resource" in s.get("action", "") for s in resp.json()["suggestions"])


def test_analysis_generic_suggestions_when_no_pattern() -> None:
    mock_svc = MagicMock()
    mock_svc.get_goal = AsyncMock(return_value={
        "goal": "Do something",
        "status": "failed",
        "verification_feedback": "unknown error occurred",
        "steps": [],
    })
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/insights/analysis/g5", headers=_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()["suggestions"]) >= 1


def test_analysis_with_steps_info() -> None:
    mock_svc = MagicMock()
    mock_svc.get_goal = AsyncMock(return_value={
        "goal": "Multi-step",
        "status": "failed",
        "verification_feedback": "",
        "steps": [
            {"description": "Step 1", "output": "output1"},
            {"description": "Last step", "output": "failed output"},
        ],
        "iterations": 5,
        "cost_usd": 0.12,
    })
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/insights/analysis/g6", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["iterations_used"] == 5
    assert data["cost_usd"] == 0.12


# ── POST /insights/query ──────────────────────────────────────────────────────

def test_nl_query_no_goal_service_returns_empty() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/insights/query",
        json={"query": "show me goals"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_nl_query_today_filter() -> None:
    mock_svc = MagicMock()
    mock_svc.list_goals = AsyncMock(return_value={"goals": []})
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/insights/query",
        json={"query": "show me goals that failed today"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["query_parsed"]["days"] == 1
    assert data["query_parsed"]["status_filter"] == "failed"


def test_nl_query_week_filter() -> None:
    mock_svc = MagicMock()
    mock_svc.list_goals = AsyncMock(return_value={"goals": []})
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/insights/query",
        json={"query": "goals this week"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["query_parsed"]["days"] == 7


def test_nl_query_month_filter() -> None:
    mock_svc = MagicMock()
    mock_svc.list_goals = AsyncMock(return_value={"goals": []})
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/insights/query",
        json={"query": "completed goals this month"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["query_parsed"]["days"] == 30
    assert data["query_parsed"]["status_filter"] == "complete"


def test_nl_query_year_filter() -> None:
    mock_svc = MagicMock()
    mock_svc.list_goals = AsyncMock(return_value={"goals": []})
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/insights/query",
        json={"query": "goals from this year"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["query_parsed"]["days"] == 365


def test_nl_query_default_30_days() -> None:
    mock_svc = MagicMock()
    mock_svc.list_goals = AsyncMock(return_value={"goals": []})
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/insights/query",
        json={"query": "executing goals"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["query_parsed"]["days"] == 30
    assert resp.json()["query_parsed"]["status_filter"] == "executing"


def test_nl_query_invalid_entity_returns_422() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/insights/query",
        json={"query": "test", "entity": "invalid_entity"},
        headers=_HEADERS,
    )
    assert resp.status_code == 422


def test_nl_query_agents_entity() -> None:
    mock_svc = MagicMock()
    mock_svc.list_goals = AsyncMock(return_value={"goals": []})
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/insights/query",
        json={"query": "show agents", "entity": "agents"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["query_parsed"]["entity"] == "agents"


def test_nl_query_respects_limit() -> None:
    mock_svc = MagicMock()
    mock_svc.list_goals = AsyncMock(return_value={"goals": [{"id": f"g{i}"} for i in range(50)]})
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/insights/query",
        json={"query": "all goals", "limit": 5},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    # At most 5 results
    assert resp.json()["total"] <= 5


def test_nl_query_goal_service_raises_returns_empty() -> None:
    mock_svc = MagicMock()
    mock_svc.list_goals = AsyncMock(side_effect=RuntimeError("DB error"))
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.post(
        "/insights/query",
        json={"query": "all goals"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["results"] == []


# ── GET /insights/agent-health/{agent_id} ────────────────────────────────────

def test_agent_health_no_goal_service_returns_defaults() -> None:
    client = TestClient(_make_app())
    resp = client.get("/insights/agent-health/agent-xyz", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == "agent-xyz"
    assert "health" in data
    health = data["health"]
    assert "speed" in health
    assert "accuracy" in health
    assert "cost_efficiency" in health
    assert "tool_coverage" in health
    assert "success_rate" in health
    assert "coherence" in health
    assert data["sample_size"] == 0


def test_agent_health_with_no_db_on_goal_service() -> None:
    mock_svc = MagicMock()
    mock_svc._db = None
    client = TestClient(_make_app(goal_service=mock_svc))
    resp = client.get("/insights/agent-health/agent-1", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["sample_size"] == 0


# ── GET /insights/benchmarks ──────────────────────────────────────────────────

def test_benchmarks_returns_platform_data() -> None:
    client = TestClient(_make_app())
    resp = client.get("/insights/benchmarks", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "platform_avg_success_rate" in data
    assert "platform_avg_cost_usd" in data
    assert "percentile_bands" in data
    assert "p25" in data["percentile_bands"]
    assert "p50" in data["percentile_bands"]
    assert "p75" in data["percentile_bands"]
    assert "p90" in data["percentile_bands"]
    assert "sample_note" in data


def test_benchmarks_values_are_reasonable() -> None:
    client = TestClient(_make_app())
    data = client.get("/insights/benchmarks", headers=_HEADERS).json()
    assert 0 < data["platform_avg_success_rate"] <= 1.0
    assert data["platform_avg_cost_usd"] > 0
    assert data["platform_avg_duration_s"] > 0
