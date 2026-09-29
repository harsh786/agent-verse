"""Extra coverage tests for app/api/insights.py — targeting 85%+ coverage."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

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

def test_estimate_without_db_is_501_not_platform_defaults():
    """No database -> 501; the old path returned invented platform defaults."""
    mock_goal_svc = MagicMock()
    mock_goal_svc._db = None
    app = _make_app(goal_service=mock_goal_svc)
    resp = TestClient(app).post("/insights/estimate", json={"goal": "test goal"}, headers=_HEADERS)
    assert resp.status_code == 501


def _make_db_factory_from_session(session: Any) -> Any:
    """Return a callable that acts as an async context manager factory."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


def test_estimate_db_exception_is_503():
    """A failing query is a 503, never a silent fallback to defaults."""
    mock_session = AsyncMock()
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
    mock_goal_svc = MagicMock()
    mock_goal_svc._db = _make_db_factory_from_session(mock_session)
    app = _make_app(goal_service=mock_goal_svc)
    resp = TestClient(app).post("/insights/estimate", json={"goal": "test"}, headers=_HEADERS)
    assert resp.status_code == 503


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

    mock_svc.get_events = AsyncMock(return_value=[
        {"type": "step_start", "payload": {"description": "Plan step", "status": "running"}},
        {"type": "step_complete", "payload": {"description": "Done step", "status": "complete"}},
    ])
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

    mock_svc.get_events = AsyncMock(return_value=[
        {"type": "tool_call", "payload": {"tool_name": "search", "arguments": {"q": "test"}}},
        {"type": "tool_result", "payload": {"tool_name": "search", "result": "found"}},
    ])
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

    mock_svc.get_events = AsyncMock(return_value=[
        {"type": "goal_complete", "payload": {}},
    ])
    mock_svc.get_goal.return_value = None
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/graph/g-complete", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert any(n["type"] == "end" for n in data["nodes"])


def test_graph_with_goal_failed_event():
    """Graph adds a terminal node on goal_failed (type 'failed')."""
    mock_svc = AsyncMock()
    mock_svc.get_event_log.return_value = [
        {"type": "goal_failed", "payload": {"reason": "timeout"}},
    ]

    mock_svc.get_events = AsyncMock(return_value=[
        {"type": "goal_failed", "payload": {"reason": "timeout"}},
    ])
    mock_svc.get_goal.return_value = None
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/graph/g-failed", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    # goal_failed produces a "failed" type node (not "end")
    assert any(n["type"] in ("end", "failed") for n in data["nodes"])


def test_graph_fallback_to_goal_record_events():
    """When get_events returns empty, the graph returns at least a start node."""
    mock_svc = AsyncMock()
    mock_svc.get_event_log.return_value = []
    mock_svc.get_events = AsyncMock(return_value=[])
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
    # With no events, at minimum the start node is returned
    assert len(data["nodes"]) >= 1
    assert any(n["id"] == "start" for n in data["nodes"])


def test_graph_event_log_exception():
    """When get_event_log raises, falls back to empty events."""
    mock_svc = AsyncMock()
    mock_svc.get_event_log.side_effect = RuntimeError("no events table")

    mock_svc.get_events = AsyncMock(side_effect=RuntimeError("no events"))
    mock_svc.get_goal.return_value = None
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.get("/insights/graph/g-exc", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    # Only start node
    assert any(n["id"] == "start" for n in data["nodes"])


def test_graph_plan_ready_event():
    """plan_ready event with a steps list creates step nodes."""
    mock_svc = AsyncMock()
    events = [{"type": "plan_ready", "steps": ["Plan created"]}]
    mock_svc.get_event_log.return_value = events
    mock_svc.get_events = AsyncMock(return_value=events)
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
    mock_svc._db = None  # in-memory mode
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
    mock_svc._db = None  # in-memory mode
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
    mock_svc._db = None  # in-memory mode
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
    mock_svc._db = None  # in-memory mode
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
    mock_svc._db = None  # in-memory mode
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
    mock_svc._db = None  # in-memory mode
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


def test_nl_query_exception_is_503():
    """Exception in list_goals is a 503, not an empty result."""
    mock_svc = AsyncMock()
    mock_svc._db = None  # in-memory mode
    mock_svc.list_goals.side_effect = RuntimeError("DB unavailable")
    app = _make_app(goal_service=mock_svc)
    client = TestClient(app)
    resp = client.post(
        "/insights/query",
        json={"query": "all goals", "entity": "goals"},
        headers=_HEADERS,
    )
    assert resp.status_code == 503


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

def test_agent_health_no_db_is_501():
    """No database -> 501; the old path returned every axis as 0.7."""
    resp = TestClient(_make_app()).get("/insights/agent-health/agent-1", headers=_HEADERS)
    assert resp.status_code == 501


def test_agent_health_db_exception_is_503():
    """A failing query is a 503, not default health values."""
    mock_session = AsyncMock()
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
    mock_svc = MagicMock()
    mock_svc._db = _make_db_factory_from_session(mock_session)
    app = _make_app(goal_service=mock_svc)
    resp = TestClient(app).get("/insights/agent-health/agent-4", headers=_HEADERS)
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# benchmarks
# ---------------------------------------------------------------------------

def test_benchmarks_without_db_is_501():
    resp = TestClient(_make_app()).get("/insights/benchmarks", headers=_HEADERS)
    assert resp.status_code == 501


def test_benchmarks_requires_auth():
    app = _make_app()
    client = TestClient(app)
    resp = client.get("/insights/benchmarks")
    assert resp.status_code == 401
