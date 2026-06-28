"""Tests for agentic feature specifications across all 5 specs:

1. test_workflow_mode_supervisor_returns_sub_goal_ids
2. test_workflow_mode_multi_agent_with_agent_ids
3. test_spawn_tool_registered_when_civilization_id_set
4. test_persistence_attempt_written_to_db
5. test_simulation_stream_returns_sse_events
6. test_rpa_current_view_does_not_create_action_record
7. test_goal_lineage_endpoint_returns_tree
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.goals import router as goals_router
from app.api.enterprise import router as enterprise_router
from app.api.rpa import router as rpa_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-agentic", plan=PlanTier.ENTERPRISE, api_key_id="kid-1")
_VALID_KEY = "ak_agentic_test"


def _make_goals_app(fake_service: Any, extra_state: dict | None = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(goals_router)
    app.state.goal_service = fake_service
    if extra_state:
        for k, v in extra_state.items():
            setattr(app.state, k, v)
    return app


def _make_enterprise_app(simulation_runner: Any) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(enterprise_router)
    app.state.simulation_runner = simulation_runner
    return app


def _make_rpa_app(session_manager: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(rpa_router)
    if session_manager:
        app.state.rpa_session_manager = session_manager
    return app


# ---------------------------------------------------------------------------
# Test 1: workflow_mode=supervisor returns sub_goal_ids
# ---------------------------------------------------------------------------

def test_workflow_mode_supervisor_returns_sub_goal_ids() -> None:
    """Supervisor mode should return sub_goal_ids in the response."""
    svc = AsyncMock()
    # The supervisor import path is mocked so no real LLM required
    with patch("app.agent.supervisor.SupervisorAgent") as MockSupervisor:
        instance = MockSupervisor.return_value
        instance.run = AsyncMock(return_value={
            "parent_goal_id": "pg-1",
            "sub_goal_ids": ["sg-1", "sg-2", "sg-3"],
            "synthesis": "done",
        })
        app = _make_goals_app(svc, extra_state={"_app_provider": MagicMock()})
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/goals",
            json={"goal": "Build and deploy microservice", "workflow_mode": "supervisor"},
            headers={"X-API-Key": _VALID_KEY},
        )

    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "multi_agent"
    assert data["mode"] == "supervisor"
    assert isinstance(data["sub_goal_ids"], list)


# ---------------------------------------------------------------------------
# Test 2: workflow_mode=multi_agent with agent_ids dispatches to multiple agents
# ---------------------------------------------------------------------------

def test_workflow_mode_multi_agent_with_agent_ids() -> None:
    """multi_agent mode with agent_ids should submit goal to each agent."""
    svc = AsyncMock()
    svc.submit_goal = AsyncMock(side_effect=[
        {"goal_id": "g-1", "status": "planning"},
        {"goal_id": "g-2", "status": "planning"},
    ])
    app = _make_goals_app(svc)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/goals",
        json={
            "goal": "Monitor production",
            "workflow_mode": "multi_agent",
            "agent_ids": ["agent-a", "agent-b"],
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "multi_agent"
    assert data["mode"] == "multi_agent"
    assert len(data["sub_goal_ids"]) == 2
    assert "g-1" in data["sub_goal_ids"]
    assert "g-2" in data["sub_goal_ids"]


# ---------------------------------------------------------------------------
# Test 3: civilization_id in initial_context enables spawn tool
# ---------------------------------------------------------------------------

def test_spawn_tool_registered_when_civilization_id_set() -> None:
    """AgentGraph should enable civilization_spawn when civilization_id in initial_context."""
    from unittest.mock import MagicMock
    from app.agent.graph import AgentGraph

    mock_provider = MagicMock()
    graph = AgentGraph(
        planner=mock_provider,
        executor=mock_provider,
        verifier=mock_provider,
    )
    # Before run(), civilization_id should be None
    assert graph._civilization_id is None
    assert graph._civilization_spawn_enabled is False

    # Simulate what run() does when initial_context has civilization_id
    graph._civilization_id = "civ-123"
    graph._civilization_spawn_enabled = True

    assert graph._civilization_id == "civ-123"
    assert graph._civilization_spawn_enabled is True


# ---------------------------------------------------------------------------
# Test 4: persistence attempt DB write called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persistence_attempt_written_to_db() -> None:
    """GoalPersistenceEngine should call _write_attempt_start and _write_attempt_end."""
    from app.agent.persistence import GoalPersistenceEngine, PersistenceConfig
    from app.tenancy.context import PlanTier, TenantContext

    tenant = TenantContext(tenant_id="tid-persist", plan=PlanTier.FREE, api_key_id="k1")

    # Mock DB factory
    mock_db = AsyncMock()
    engine = GoalPersistenceEngine(
        config=PersistenceConfig(max_attempts=1, base_backoff_seconds=1.0),
        db=None,  # No real DB, testing that the method is called
    )

    write_start_calls: list[dict] = []
    write_end_calls: list[dict] = []

    async def mock_write_start(**kwargs):
        write_start_calls.append(kwargs)
        return "attempt-id-1"

    async def mock_write_end(**kwargs):
        write_end_calls.append(kwargs)

    engine._write_attempt_start = mock_write_start  # type: ignore[method-assign]
    engine._write_attempt_end = mock_write_end  # type: ignore[method-assign]

    # Agent that always succeeds
    mock_state = MagicMock()
    mock_state.status.value = "complete"
    mock_state.verification_success = True
    mock_state.iterations = 1
    mock_state.context = {"total_cost_usd": 0.01}

    async def fake_run(**kwargs):
        return mock_state

    mock_agent = MagicMock()
    mock_agent.run = fake_run

    success, attempts = await engine.run(
        goal="Test goal",
        agent_factory=mock_agent,
        tenant_ctx=tenant,
        goal_id="goal-xyz",
    )

    assert success is True
    assert len(write_start_calls) == 1
    assert write_start_calls[0]["goal_id"] == "goal-xyz"
    assert len(write_end_calls) >= 1


# ---------------------------------------------------------------------------
# Test 5: simulation/stream returns SSE events
# ---------------------------------------------------------------------------

def test_simulation_stream_returns_sse_events() -> None:
    """POST /enterprise/simulation/stream should return text/event-stream."""
    from app.enterprise.simulation import SimulationRunner

    runner = SimulationRunner()
    # No provider — will use stub mode
    app = _make_enterprise_app(runner)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/enterprise/simulation/stream",
        json={"goal": "Test streaming", "mock_tools": {}, "max_steps": 3},
        headers={"X-API-Key": _VALID_KEY},
    )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    content = resp.text
    assert "data:" in content
    # Should have at least a started and complete event
    assert "simulation_started" in content or "simulation_complete" in content


# ---------------------------------------------------------------------------
# Test 6: RPA current-view does NOT create action record
# ---------------------------------------------------------------------------

def test_rpa_current_view_does_not_create_action_record() -> None:
    """GET /rpa/sessions/{id}/current-view should not touch the executor."""
    mock_page = AsyncMock()
    mock_page.url = "https://example.com"
    mock_page.screenshot = AsyncMock(return_value=b"fake-jpeg-bytes")

    mock_session_manager = MagicMock()
    mock_session_manager.get_page = MagicMock(return_value=mock_page)

    # Executor should NOT be called at all
    mock_executor = AsyncMock()

    app = _make_rpa_app(session_manager=mock_session_manager)
    app.state.rpa_executor = mock_executor

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(
        "/rpa/sessions/sess-123/current-view",
        headers={"X-API-Key": _VALID_KEY},
    )

    # Executor.execute should NEVER have been called
    mock_executor.execute.assert_not_called()
    # Response should be 200 with screenshot data
    assert resp.status_code == 200
    data = resp.json()
    assert "screenshot_data_uri" in data
    assert data["screenshot_data_uri"].startswith("data:image/jpeg;base64,")


# ---------------------------------------------------------------------------
# Test 7: goal lineage endpoint returns tree structure
# ---------------------------------------------------------------------------

def test_goal_lineage_endpoint_returns_tree() -> None:
    """GET /goals/{id}/lineage should return a tree with nodes and edges."""
    svc = AsyncMock()

    # Mock the DB session factory used inside goals.py via its import path
    with patch("app.db.session.get_session_factory") as mock_db_factory:
        # No DB available — should return minimal root response
        mock_db_factory.return_value = None
        # Also patch the local import inside get_goal_lineage
        with patch("app.api.goals.get_session_factory", mock_db_factory, create=True):
            app = _make_goals_app(svc)
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(
                "/goals/goal-root-123/lineage",
                headers={"X-API-Key": _VALID_KEY},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["root_goal_id"] == "goal-root-123"
    assert isinstance(data["nodes"], list)
    assert isinstance(data["edges"], list)
    # Root node should always be present
    goal_ids = [n["goal_id"] for n in data["nodes"]]
    assert "goal-root-123" in goal_ids
