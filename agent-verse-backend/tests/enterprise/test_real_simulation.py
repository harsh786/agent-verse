"""Tests for real simulation runner with mock tools."""
from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.enterprise.simulation import SimulationRunner
from app.tenancy.context import TenantContext, PlanTier


_CTX = TenantContext(tenant_id="sim-test", plan=PlanTier.FREE, api_key_id="k1")


async def test_simulation_with_mock_tools():
    """SimulationRunner returns a complete run with steps in the result."""
    runner = SimulationRunner()
    run = await runner.start(
        goal="Fetch GitHub issues and create Jira tickets",
        mock_tools={
            "github:list_issues": [{"id": 1, "title": "Bug fix"}],
            "jira:create_issue": {"id": "PROJ-1"},
        },
        tenant_ctx=_CTX,
    )

    assert run.status in ("complete", "completed")
    assert isinstance(run.result, dict)
    assert "steps" in run.result or "simulated_steps" in run.result


async def test_simulation_steps_match_mock_tools():
    """Each provided mock_tool produces a corresponding step in the result."""
    mock_tools = {
        "github:list_issues": [{"id": 1}],
        "jira:create_issue": {"id": "PROJ-1"},
    }
    runner = SimulationRunner()
    run = await runner.start(goal="test", mock_tools=mock_tools, tenant_ctx=_CTX)

    steps = run.result.get("steps", run.result.get("simulated_steps", []))
    tool_names = [s.get("tool") for s in steps if s.get("tool")]
    for tool in mock_tools:
        assert tool in tool_names, f"Expected step for tool {tool}"


async def test_simulation_empty_mock_tools():
    """Simulation works even with no mock tools; still produces a result."""
    runner = SimulationRunner()
    run = await runner.start(goal="Do something", mock_tools={}, tenant_ctx=_CTX)
    assert run.status in ("complete", "completed")
    assert run.result is not None


async def test_simulation_get():
    """Can retrieve a simulation run by ID."""
    runner = SimulationRunner()
    run = await runner.start(goal="test retrieval", mock_tools={}, tenant_ctx=_CTX)
    fetched = runner.get(run_id=run.run_id, tenant_ctx=_CTX)
    assert fetched is not None
    assert fetched.run_id == run.run_id


def test_simulation_get_nonexistent_returns_none():
    runner = SimulationRunner()
    result = runner.get(run_id="does-not-exist", tenant_ctx=_CTX)
    assert result is None


async def test_simulation_list_runs():
    """list_runs returns all started runs."""
    runner = SimulationRunner()
    run1 = await runner.start(goal="first", mock_tools={}, tenant_ctx=_CTX)
    run2 = await runner.start(goal="second", mock_tools={}, tenant_ctx=_CTX)
    all_runs = runner.list_runs(tenant_ctx=_CTX)
    run_ids = [r.run_id for r in all_runs]
    assert run1.run_id in run_ids
    assert run2.run_id in run_ids


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def authed_client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/tenants/signup", json={"name": "Test", "email": "t@test.com"})
        assert resp.status_code == 201
        c.headers["X-API-Key"] = resp.json()["api_key"]
        yield c


async def test_simulation_api(authed_client):
    resp = await authed_client.post(
        "/enterprise/simulation",
        json={
            "goal": "Create a Jira issue for each GitHub bug",
            "mock_tools": {"jira:create_issue": {"id": "PROJ-1"}},
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "run_id" in data
    assert "status" in data
    assert data["status"] in ("complete", "completed")


async def test_simulation_api_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/enterprise/simulation", json={"goal": "test"})
        assert resp.status_code == 401


async def test_simulation_api_get_by_id(authed_client):
    """After POST /simulation, the run can be retrieved by run_id."""
    create_resp = await authed_client.post(
        "/enterprise/simulation",
        json={"goal": "check workflow"},
    )
    assert create_resp.status_code == 201
    run_id = create_resp.json()["run_id"]

    get_resp = await authed_client.get(f"/enterprise/simulation/{run_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["run_id"] == run_id


async def test_simulation_api_unknown_run_returns_404(authed_client):
    resp = await authed_client.get("/enterprise/simulation/no-such-run")
    assert resp.status_code == 404
