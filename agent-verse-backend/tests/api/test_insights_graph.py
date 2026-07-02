"""Tests for /insights/graph/{goal_id} execution graph endpoint."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.insights import router as insights_router
from app.tenancy.context import PlanTier, TenantContext

_TENANT = TenantContext(
    tenant_id="test-tenant",
    plan=PlanTier.PROFESSIONAL,
    api_key_id="test-key",
)


def _make_app(events: list[dict]) -> TestClient:
    app = FastAPI()

    async def resolve_tenant(request, call_next):
        request.state.tenant = _TENANT
        return await call_next(request)

    app.add_middleware(BaseHTTPMiddleware, dispatch=resolve_tenant)
    app.include_router(insights_router)

    mock_goal_svc = MagicMock()
    mock_goal_svc.get_event_log = AsyncMock(return_value=events)
    app.state.goal_service = mock_goal_svc
    return TestClient(app)


_TYPICAL_EVENTS = [
    {"type": "goal_started", "goal": "Find Jira issues"},
    {"type": "plan_ready", "steps": ["Search Jira", "Summarise results"]},
    {"type": "step_started", "step": "Search Jira"},
    {
        "type": "tool_call_complete",
        "tool": "jira_search_issues",
        "server_id": "jira",
        "success": True,
        "output": "[dict omitted]",
        "tool_output": {"total": 2, "issues": []},
    },
    {"type": "step_complete", "step": "Search Jira", "output": "Found 2 issues."},
    {"type": "verification_done", "success": True},
    {"type": "goal_complete"},
]


def test_graph_returns_nodes_for_actual_event_types() -> None:
    client = _make_app(_TYPICAL_EVENTS)
    resp = client.get("/insights/graph/test-goal-1")
    assert resp.status_code == 200
    data = resp.json()
    node_types = [n["type"] for n in data["nodes"]]
    assert "start" in node_types
    assert "tool" in node_types
    assert "end" in node_types


def test_graph_stats_reflect_tool_calls() -> None:
    client = _make_app(_TYPICAL_EVENTS)
    data = client.get("/insights/graph/test-goal-1").json()
    assert data["stats"]["tool_calls"] >= 1
    assert data["stats"]["unique_tools"] >= 1


def test_graph_builds_step_nodes_from_step_started_events() -> None:
    client = _make_app(_TYPICAL_EVENTS)
    data = client.get("/insights/graph/test-goal-1").json()
    node_types = [n["type"] for n in data["nodes"]]
    assert "step" in node_types


def test_graph_handles_empty_events_gracefully() -> None:
    client = _make_app([])
    data = client.get("/insights/graph/test-goal-1").json()
    assert "nodes" in data
    assert "stats" in data


def test_graph_plan_ready_creates_step_nodes() -> None:
    events = [
        {"type": "plan_ready", "steps": ["Step A", "Step B", "Step C"]},
        {"type": "goal_complete"},
    ]
    client = _make_app(events)
    data = client.get("/insights/graph/test-goal-1").json()
    labels = [n["label"] for n in data["nodes"]]
    assert "Step A" in labels
    assert "Step B" in labels
