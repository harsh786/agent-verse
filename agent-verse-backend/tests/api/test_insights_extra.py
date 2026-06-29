"""Extra coverage tests for app/api/insights.py — targeting 85%+ coverage."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.insights import router as insights_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-ins2", plan=PlanTier.PROFESSIONAL, api_key_id="kid-ins2")
_VALID_KEY = "ak_test_extra_insights"
_HEADERS = {"X-API-Key": _VALID_KEY}


def _make_app(**state_attrs: Any) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(insights_router)
    for k, v in state_attrs.items():
        setattr(app.state, k, v)
    return app


# ---------------------------------------------------------------------------
# estimate_goal
# ---------------------------------------------------------------------------

def test_estimate_with_embedder_and_goal_service_no_db():
    """estimate returns defaults when embedder exists but DB is None."""
    mock_embedder = AsyncMock()
    mock_embedder.embed.return_value = [0.1, 0.2]
    mock_goal_svc = MagicMock()
    mock_goal_svc._db = None

    app = _make_app(goal_service=mock_goal_svc, embedder=mock_embedder)
    client = TestClient(app)
    resp = client.post("/insights/estimate", json={"goal": "test goal"}, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["based_on"] == "platform_defaults"


def _make_db_factory_from_session(session: Any) -> Any:
    """Return a callable that acts as an async context manager factory."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


def test_estimate_with_embedder_and_db_rows():
    """estimate uses historical data when DB returns rows."""
    mock_embedder = AsyncMock()
    mock_embedder.embed.return_value = [0.1, 0.2]

    # Mock DB session that returns rows
    mock_row = (0.05, 30, 2, "complete")
    mock_row2 = (0.03, 20, 1, "failed")

    mock_session = AsyncMock()

    async def _execute_side_effect(stmt, params=None):
        mock_result = MagicMock()
        if "SET LOCAL" in str(stmt):
            return mock_result
        mock_result.fetchall.return_value = [mock_row, mock_row2]
        return mock_result

    mock_session.execute = AsyncMock(side_effect=_execute_side_effect)

    mock_goal_svc = MagicMock()
    mock_goal_svc._db = _make_db_factory_from_session(mock_session)

    app = _make_app(goal_service=mock_goal_svc, embedder=mock_embedder)
    client = TestClient(app)
    resp = client.post("/insights/estimate", json={"goal": "deploy microservices"}, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "estimated_cost_usd" in data


def test_estimate_embedder_returns_none():
    """estimate handles embedder returning empty/None embedding gracefully."""
    mock_embedder = AsyncMock()
    mock_embedder.embed.return_value = []

    mock_session = AsyncMock()

    mock_goal_svc = MagicMock()
    mock_goal_svc._db = _make_db_factory_from_session(mock_session)

    app = _make_app(goal_service=mock_goal_svc, embedder=mock_embedder)
    client = TestClient(app)
    resp = client.post("/insights/estimate", json={"goal": "test"}, headers=_HEADERS)
    assert resp.status_code == 200


def test_estimate_db_exception_falls_back():
    """estimate falls back to defaults when embedder raises exception."""
    mock_embedder = AsyncMock()
    mock_embedder.embed.side_effect = RuntimeError("embed failed")

    mock_session = AsyncMock()

    mock_goal_svc = MagicMock()
    mock_goal_svc._db = _make_db_factory_from_session(mock_session)

    app = _make_app(goal_service=mock_goal_svc, embedder=mock_embedder)
    client = TestClient(app)
    resp = client.post("/insights/estimate", json={"goal": "test"}, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["based_on"] == "platform_defaults"


def test_estimate_requires_non_empty_goal():
    app = _make_app()
    client = TestClient(app)
    resp = client.post("/insights/estimate", json={"goal": ""}, headers=_HEADERS)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# get_execution_graph
# ---------------------------------------------------------------------------

def test_graph_with_step_events():
    """Graph correctly builds nodes from step_start events."""
    mock_svc = AsyncMock()
    mock_svc.get_event_log.return_value = [
        {"type": "step_start", "payload": {"description": "Plan step", "status": "running"}},
        {"type": "step_complete", "payload": {"description": "Done step", "status": "complete"}},
    ]
    mock_svc.get_goal.return_value = None
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/graph/g-steps", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["nodes"]) > 1
    assert any(n["type"] == "step" for n in data["nodes"])


def test_graph_with_tool_call_events():
    """Graph correctly processes tool_call and tool_result events."""
    mock_svc = AsyncMock()
    mock_svc.get_event_log.return_value = [
        {"type": "tool_call", "payload": {"tool_name": "search", "arguments": {"q": "test"}}},
        {"type": "tool_result", "payload": {"tool_name": "search", "result": "found"}},
    ]
    mock_svc.get_goal.return_value = None
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/graph/g-tools", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["stats"]["tool_calls"] >= 1
    assert any(n["type"] == "tool" for n in data["nodes"])


def test_graph_with_goal_complete_event():
    """Graph adds end node on goal_complete."""
    mock_svc = AsyncMock()
    mock_svc.get_event_log.return_value = [
        {"type": "goal_complete", "payload": {}},
    ]
    mock_svc.get_goal.return_value = None
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/graph/g-complete", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert any(n["type"] == "end" for n in data["nodes"])


def test_graph_with_goal_failed_event():
    """Graph adds end node on goal_failed."""
    mock_svc = AsyncMock()
    mock_svc.get_event_log.return_value = [
        {"type": "goal_failed", "payload": {"reason": "timeout"}},
    ]
    mock_svc.get_goal.return_value = None
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/graph/g-failed", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert any(n["type"] == "end" for n in data["nodes"])


def test_graph_fallback_to_goal_record_events():
    """When get_event_log returns empty, falls back to goal record's events."""
    mock_svc = AsyncMock()
    mock_svc.get_event_log.return_value = []
    mock_svc.get_goal.return_value = {
        "events": [
            {"type": "step_start", "payload": {"description": "Fallback step"}},
        ]
    }
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/graph/g-fallback", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["nodes"]) > 1


def test_graph_event_log_exception():
    """When get_event_log raises, falls back to empty events."""
    mock_svc = AsyncMock()
    mock_svc.get_event_log.side_effect = RuntimeError("no events table")
    mock_svc.get_goal.return_value = None
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/graph/g-exc", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    # Only start node
    assert any(n["id"] == "start" for n in data["nodes"])


def test_graph_plan_ready_event():
    """plan_ready event is treated as a step node."""
    mock_svc = AsyncMock()
    mock_svc.get_event_log.return_value = [
        {"type": "plan_ready", "payload": {"step_description": "Plan created"}},
    ]
    mock_svc.get_goal.return_value = None
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/graph/g-planready", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert any(n["type"] == "step" for n in data["nodes"])


# ---------------------------------------------------------------------------
# analyze_failure
# ---------------------------------------------------------------------------

def test_analysis_timeout_suggestion():
    """Timeout keyword triggers timeout suggestion."""
    mock_svc = AsyncMock()
    mock_svc.get_goal.return_value = {
        "goal": "Run long task",
        "status": "failed",
        "verification_feedback": "request timed out after 30s",
        "steps": [],
        "iterations": 2,
        "cost_usd": 0.01,
    }
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/analysis/g-timeout", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert any("timeout" in s["action"].lower() or "Timeout" in s["action"] for s in data["suggestions"])


def test_analysis_permission_suggestion():
    """Permission keyword triggers permissions suggestion."""
    mock_svc = AsyncMock()
    mock_svc.get_goal.return_value = {
        "goal": "Deploy to prod",
        "status": "failed",
        "verification_feedback": "403 permission denied",
        "steps": [],
        "iterations": 1,
        "cost_usd": 0.0,
    }
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/analysis/g-perm", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert any("permission" in s["action"].lower() or "Permissions" in s["action"] for s in data["suggestions"])


def test_analysis_not_found_suggestion():
    """404 keyword triggers missing resource suggestion."""
    mock_svc = AsyncMock()
    mock_svc.get_goal.return_value = {
        "goal": "Fetch resource",
        "status": "failed",
        "verification_feedback": "resource not found 404",
        "steps": [],
        "iterations": 1,
        "cost_usd": 0.0,
    }
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/analysis/g-404", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert any("resource" in s["action"].lower() or "Missing" in s["action"] for s in data["suggestions"])


def test_analysis_default_suggestions_for_unknown_failure():
    """No keyword match falls through to default suggestions."""
    mock_svc = AsyncMock()
    mock_svc.get_goal.return_value = {
        "goal": "Do something",
        "status": "failed",
        "verification_feedback": "something went wrong",
        "steps": [{"description": "last step", "output": "error"}],
        "iterations": 5,
        "cost_usd": 0.05,
    }
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/analysis/g-default", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["suggestions"]) >= 3


def test_analysis_goal_exception_returns_404():
    """When get_goal raises, returns 404."""
    mock_svc = AsyncMock()
    mock_svc.get_goal.side_effect = Exception("DB error")
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/analysis/g-exc", headers=_HEADERS)
    assert resp.status_code == 404


def test_analysis_with_llm_provider():
    """When provider exists and returns JSON, uses LLM suggestions."""
    mock_svc = AsyncMock()
    mock_svc.get_goal.return_value = {
        "goal": "Call API",
        "status": "failed",
        "verification_feedback": "rate limit 429",
        "steps": [],
        "iterations": 3,
        "cost_usd": 0.02,
    }

    mock_provider = AsyncMock()
    import json
    mock_provider.complete.return_value = MagicMock(
        content=json.dumps({
            "failure_reason": "Rate limit hit",
            "suggestions": [
                {"action": "Backoff", "description": "Add exponential backoff"},
            ],
        })
    )

    app = _make_app(goal_service=mock_svc)
    app.state._app_provider = mock_provider
    client = TestClient(app)
    resp = client.get("/insights/analysis/g-llm", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "failure_reason" in data


def test_analysis_llm_provider_json_error_falls_back():
    """When LLM returns invalid JSON, falls back to heuristics."""
    mock_svc = AsyncMock()
    mock_svc.get_goal.return_value = {
        "goal": "Do task",
        "status": "failed",
        "verification_feedback": "some error",
        "steps": [],
        "iterations": 1,
        "cost_usd": 0.01,
    }

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = MagicMock(content="not json at all")

    app = _make_app(goal_service=mock_svc)
    app.state._app_provider = mock_provider
    client = TestClient(app)
    resp = client.get("/insights/analysis/g-badjson", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["suggestions"]) > 0


# ---------------------------------------------------------------------------
# natural_language_query
# ---------------------------------------------------------------------------

def test_nl_query_no_goal_service():
    """Returns empty results when no goal_service is configured."""
    app = _make_app()
    client = TestClient(app)
    resp = client.post(
        "/insights/query",
        json={"query": "show me all goals", "entity": "goals"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_nl_query_today_filter():
    """'today' in query sets days=1."""
    mock_svc = AsyncMock()
    mock_svc.list_goals.return_value = {"goals": []}
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.post(
        "/insights/query",
        json={"query": "show me today's goals", "entity": "goals"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["query_parsed"]["days"] == 1


def test_nl_query_week_filter():
    """'week' in query sets days=7."""
    mock_svc = AsyncMock()
    mock_svc.list_goals.return_value = {"goals": []}
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.post(
        "/insights/query",
        json={"query": "goals from this week", "entity": "goals"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["query_parsed"]["days"] == 7


def test_nl_query_year_filter():
    """'year' in query sets days=365."""
    mock_svc = AsyncMock()
    mock_svc.list_goals.return_value = {"goals": []}
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.post(
        "/insights/query",
        json={"query": "goals from this year", "entity": "goals"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["query_parsed"]["days"] == 365


def test_nl_query_status_filter():
    """'failed' in query filters by status."""
    mock_svc = AsyncMock()
    mock_svc.list_goals.return_value = {
        "goals": [
            {"goal_id": "g1", "status": "failed", "goal": "task1"},
            {"goal_id": "g2", "status": "complete", "goal": "task2"},
        ]
    }
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.post(
        "/insights/query",
        json={"query": "show me failed goals", "entity": "goals"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert all(r["status"] == "failed" for r in results)


def test_nl_query_cost_filter():
    """'cost over $0.05' parses and filters by cost."""
    mock_svc = AsyncMock()
    mock_svc.list_goals.return_value = {
        "goals": [
            {"goal_id": "g1", "status": "complete", "cost_usd": 0.10},
            {"goal_id": "g2", "status": "complete", "cost_usd": 0.01},
        ]
    }
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.post(
        "/insights/query",
        json={"query": "goals that cost more than $0.05", "entity": "goals"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["query_parsed"]["cost_min"] == 0.05
    results = data["results"]
    assert all(r["cost_usd"] > 0.05 for r in results)


def test_nl_query_list_response():
    """goal_svc returns list directly (not dict)."""
    mock_svc = AsyncMock()
    mock_svc.list_goals.return_value = [
        {"goal_id": "g1", "status": "complete"},
    ]
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.post(
        "/insights/query",
        json={"query": "all goals", "entity": "goals"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200


def test_nl_query_exception_returns_empty():
    """Exception in list_goals returns empty results."""
    mock_svc = AsyncMock()
    mock_svc.list_goals.side_effect = RuntimeError("DB unavailable")
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.post(
        "/insights/query",
        json={"query": "all goals", "entity": "goals"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_nl_query_invalid_entity():
    """Invalid entity type is rejected."""
    app = _make_app()
    client = TestClient(app)
    resp = client.post(
        "/insights/query",
        json={"query": "all", "entity": "invalid_entity"},
        headers=_HEADERS,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# get_agent_health
# ---------------------------------------------------------------------------

def test_agent_health_no_goal_service():
    """Returns default health values when no goal_service."""
    app = _make_app()
    client = TestClient(app)
    resp = client.get("/insights/agent-health/agent-1", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["sample_size"] == 0
    assert "health" in data
    assert all(v == 0.7 for v in data["health"].values())


def _make_db_factory(session: Any) -> Any:
    """Return a callable that acts as an async context manager factory."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


def test_agent_health_with_db_no_rows():
    """Returns default health when DB returns no rows."""
    mock_session = AsyncMock()

    async def _execute_se(stmt, params=None):
        mock_result = MagicMock()
        if "SET LOCAL" in str(stmt):
            return mock_result
        mock_result.fetchone.return_value = None
        return mock_result

    mock_session.execute = AsyncMock(side_effect=_execute_se)

    mock_svc = MagicMock()
    mock_svc._db = _make_db_factory(mock_session)

    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/agent-health/agent-2", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["sample_size"] == 0


def test_agent_health_with_db_rows():
    """Returns computed health when DB returns rows."""
    mock_session = AsyncMock()

    # Row: total, accuracy, speed, coherence, safety, success_rate
    fake_row = (10, 0.85, 0.75, 0.90, 0.80, 0.88)

    async def _execute_se(stmt, params=None):
        mock_result = MagicMock()
        if "SET LOCAL" in str(stmt):
            return mock_result
        mock_result.fetchone.return_value = fake_row
        return mock_result

    mock_session.execute = AsyncMock(side_effect=_execute_se)

    mock_svc = MagicMock()
    mock_svc._db = _make_db_factory(mock_session)

    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/agent-health/agent-3", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["sample_size"] == 10
    assert data["health"]["accuracy"] == 0.85


def test_agent_health_db_exception():
    """Returns default health when DB raises."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))

    mock_svc = MagicMock()
    mock_svc._db = _make_db_factory(mock_session)

    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/agent-health/agent-4", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["sample_size"] == 0


# ---------------------------------------------------------------------------
# benchmarks
# ---------------------------------------------------------------------------

def test_benchmarks_returns_data():
    app = _make_app()
    client = TestClient(app)
    resp = client.get("/insights/benchmarks", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "platform_avg_success_rate" in data
    assert "percentile_bands" in data
    assert "p50" in data["percentile_bands"]


def test_benchmarks_requires_auth():
    app = _make_app()
    client = TestClient(app)
    resp = client.get("/insights/benchmarks")
    assert resp.status_code == 401
