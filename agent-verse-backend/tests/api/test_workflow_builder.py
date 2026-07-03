"""Tests for POST /workflows/generate and the fixed POST /workflows/{id}/run.

Covers:
1. generate returns nodes and edges
2. generate requires a goal field
3. run invokes WorkflowExecutor when steps present
4. run dry_run returns plan_only
5. run returns a run_id
6. node types in generated workflow (trigger + end always present)
7. parallel steps in WorkflowPlan execution_waves
8. generate falls back to heuristic plan with no LLM provider
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.workflows import _WorkflowStore
from app.api.workflows import router as workflows_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

# ── Fixtures ──────────────────────────────────────────────────────────────────

_CTX = TenantContext(tenant_id="tid-wfb", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "ak_workflow_builder123"
_HEADERS = {"X-API-Key": _VALID_KEY}


def _make_app(
    goal_service: Any = None,
    provider: Any = None,
) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(workflows_router)
    app.state.workflow_store = _WorkflowStore()
    if goal_service is not None:
        app.state.goal_service = goal_service
    if provider is not None:
        app.state._app_provider = provider
    return app


# ── Test: generate returns nodes and edges ────────────────────────────────────

def test_generate_returns_nodes_and_edges() -> None:
    """POST /workflows/generate must return a canvas-ready {nodes, edges} payload."""
    client = TestClient(_make_app())
    resp = client.post(
        "/workflows/generate",
        json={"goal": "Fetch Jira issues and post a Slack summary"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "nodes" in data, "Response must have 'nodes' key"
    assert "edges" in data, "Response must have 'edges' key"
    assert isinstance(data["nodes"], list)
    assert isinstance(data["edges"], list)
    # Heuristic plan always has at least trigger + 1 step + end
    assert len(data["nodes"]) >= 2


# ── Test: generate requires goal ──────────────────────────────────────────────

def test_generate_requires_goal() -> None:
    """POST /workflows/generate without a 'goal' field → 422 Unprocessable Entity."""
    client = TestClient(_make_app())
    resp = client.post("/workflows/generate", json={}, headers=_HEADERS)
    assert resp.status_code == 422


# ── Test: run invokes WorkflowExecutor ────────────────────────────────────────

def test_run_uses_workflow_executor() -> None:
    """When the workflow definition has steps, WorkflowExecutor.execute() must be called."""
    app = _make_app()
    client = TestClient(app)

    # Create a workflow with step definitions
    wf_resp = client.post(
        "/workflows",
        json={
            "name": "DAG Workflow",
            "definition": {
                "steps": [
                    {"id": "s1", "description": "Fetch data", "tool": "", "depends_on": []},
                    {"id": "s2", "description": "Process", "tool": "", "depends_on": ["s1"]},
                ]
            },
        },
        headers=_HEADERS,
    )
    assert wf_resp.status_code == 201
    wf_id = wf_resp.json()["id"]

    # Patch WorkflowExecutor at the module level so the local import picks it up
    mock_exec_instance = MagicMock()
    mock_exec_instance.execute = AsyncMock(
        return_value={
            "status": "complete",
            "steps_executed": 2,
            "waves": 2,
            "results": {},
            "summary": "Done",
        }
    )
    mock_exec_class = MagicMock(return_value=mock_exec_instance)

    import app.agent.workflow_executor as _wex

    original = _wex.WorkflowExecutor
    _wex.WorkflowExecutor = mock_exec_class  # type: ignore[attr-defined]
    try:
        resp = client.post(f"/workflows/{wf_id}/run", headers=_HEADERS)
    finally:
        _wex.WorkflowExecutor = original  # type: ignore[attr-defined]

    assert resp.status_code == 202
    mock_exec_instance.execute.assert_called_once()
    assert resp.json()["status"] == "complete"


# ── Test: dry_run returns plan only ──────────────────────────────────────────

def test_run_dry_run_returns_plan_only() -> None:
    """POST /workflows/{id}/run?dry_run=true must return status='dry_run'."""
    app = _make_app()
    client = TestClient(app)

    wf_resp = client.post("/workflows", json={"name": "DryTest"}, headers=_HEADERS)
    assert wf_resp.status_code == 201
    wf_id = wf_resp.json()["id"]

    resp = client.post(f"/workflows/{wf_id}/run?dry_run=true", headers=_HEADERS)
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "dry_run"
    assert data["workflow_id"] == wf_id


# ── Test: run returns a run_id ────────────────────────────────────────────────

def test_run_returns_run_id() -> None:
    """POST /workflows/{id}/run must always return a non-empty 'run_id'."""
    app = _make_app()
    client = TestClient(app)

    wf_resp = client.post("/workflows", json={"name": "RunIdTest"}, headers=_HEADERS)
    assert wf_resp.status_code == 201
    wf_id = wf_resp.json()["id"]

    resp = client.post(f"/workflows/{wf_id}/run?dry_run=true", headers=_HEADERS)
    assert resp.status_code == 202
    data = resp.json()
    assert "run_id" in data
    assert data["run_id"]  # non-empty


# ── Test: node types in generated workflow ────────────────────────────────────

def test_node_types_in_generated_workflow() -> None:
    """Every generated workflow must have a 'trigger' and an 'end' node."""
    client = TestClient(_make_app())
    resp = client.post(
        "/workflows/generate",
        json={"goal": "Send a weekly summary email"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    node_types = {n["type"] for n in data["nodes"]}
    assert "trigger" in node_types, "Generated workflow must have a trigger node"
    assert "end" in node_types, "Generated workflow must have an end node"


# ── Test: parallel steps in execution_waves ───────────────────────────────────

def test_workflow_executor_parallel_steps() -> None:
    """Steps with no mutual dependency should land in the same execution wave."""
    from app.agent.workflow_planner import WorkflowPlan, WorkflowStep

    plan = WorkflowPlan(
        goal="Parallel test",
        steps=[
            WorkflowStep(id="s1", description="Step A", depends_on=[]),
            WorkflowStep(id="s2", description="Step B", depends_on=[]),
            WorkflowStep(id="s3", description="Step C", depends_on=["s1", "s2"]),
        ],
    )
    waves = plan.execution_waves()

    # s1 and s2 have no deps → must be in the first wave together
    assert len(waves) >= 2
    first_wave_ids = {s.id for s in waves[0]}
    assert "s1" in first_wave_ids, "s1 (no deps) must be in the first wave"
    assert "s2" in first_wave_ids, "s2 (no deps) must be in the first wave"
    assert "s3" not in first_wave_ids, "s3 (depends on s1, s2) must NOT be in the first wave"

    # s3 should appear in a later wave
    later_ids = {s.id for wave in waves[1:] for s in wave}
    assert "s3" in later_ids, "s3 must eventually be executed"


# ── Test: generate with fake/no provider ─────────────────────────────────────

def test_generate_with_fake_provider() -> None:
    """generate falls back to heuristic plan when no LLM provider is configured."""
    # _make_app with provider=None → WorkflowPlanner uses heuristic_plan
    client = TestClient(_make_app(provider=None))
    resp = client.post(
        "/workflows/generate",
        json={"goal": "A simple single-step task"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "nodes" in data
    # Heuristic plan → trigger + 1 heuristic step + end = 3 nodes
    assert len(data["nodes"]) >= 2
    # First node should be trigger
    assert data["nodes"][0]["type"] == "trigger"
    # Last node should be end
    assert data["nodes"][-1]["type"] == "end"
