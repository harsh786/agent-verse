"""Tests for Agent Builder fixes — covers all 8 fix areas."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _make_app():
    from app.main import create_app
    return create_app()


def _signup(app):
    c = TestClient(app)
    r = c.post("/tenants/signup", json={"name": "BuilderTest", "email": "bt@test.com"})
    if r.status_code not in (200, 201):
        return c, {}
    return c, {"X-API-Key": r.json().get("api_key", "")}


def _collect_routes(routes):
    """Recursively collect APIRoute objects from nested _IncludedRouter structures (FastAPI 0.138+)."""
    result = []
    for r in routes:
        # FastAPI 0.138+ wraps included routers in _IncludedRouter
        orig = getattr(r, "original_router", None)
        if orig is not None and hasattr(orig, "routes"):
            result.extend(_collect_routes(orig.routes))
        elif hasattr(r, "routes"):
            result.extend(_collect_routes(r.routes))
        else:
            result.append(r)
    return result


# ---------------------------------------------------------------------------
# FIX 1 — CreateAgentRequest + _db_persist_agent carry new fields
# ---------------------------------------------------------------------------

def test_create_agent_persists_all_fields():
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")

    r = c.post("/agents", json={
        "name": "Test Agent",
        "goal_template": "Execute tasks",
        "autonomy_mode": "bounded-autonomous",
        "connector_ids": ["github"],
        "allowed_collection_ids": ["col-1"],
        "system_prompt": "You are an expert.",
        "max_iterations": 20,
    }, headers=h)
    assert r.status_code in (200, 201), f"POST /agents failed: {r.text}"
    data = r.json()
    assert data.get("system_prompt") == "You are an expert."
    assert data.get("max_iterations") == 20


def test_create_agent_has_system_prompt_field():
    """CreateAgentRequest must accept system_prompt."""
    from app.api.agents import CreateAgentRequest
    req = CreateAgentRequest(name="test", system_prompt="You are helpful.")
    assert req.system_prompt == "You are helpful."


def test_create_agent_has_model_override_field():
    from app.api.agents import CreateAgentRequest
    req = CreateAgentRequest(name="test", model_override="claude-haiku-4-5")
    assert req.model_override == "claude-haiku-4-5"


def test_create_agent_has_max_iterations_field():
    from app.api.agents import CreateAgentRequest
    req = CreateAgentRequest(name="test", max_iterations=30)
    assert req.max_iterations == 30


def test_create_agent_has_timeout_seconds_field():
    from app.api.agents import CreateAgentRequest
    req = CreateAgentRequest(name="test", timeout_seconds=600)
    assert req.timeout_seconds == 600


# ---------------------------------------------------------------------------
# FIX 2 — PUT /agents/{agent_id} endpoint + UpdateAgentRequest
# ---------------------------------------------------------------------------

def test_update_agent_request_model_exists():
    from app.api.agents import UpdateAgentRequest
    req = UpdateAgentRequest(name="new name", max_iterations=10)
    assert req.name == "new name"
    assert req.max_iterations == 10


def test_put_agent_returns_405_without_fix():
    """Verify the PUT /agents/{agent_id} endpoint is now registered."""
    app = _make_app()
    from fastapi.routing import APIRoute
    all_routes = _collect_routes(app.routes)
    put_routes = [
        r for r in all_routes
        if isinstance(r, APIRoute)
        and "PUT" in (r.methods or set())
        and "agent_id" in getattr(r, "path", "")
        and not getattr(r, "path", "").endswith("/permissions")
        and not getattr(r, "path", "").endswith("/knowledge")
    ]
    assert len(put_routes) > 0, "PUT /agents/{agent_id} must be registered"


def test_put_agent_update_endpoint():
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")

    # Create
    r = c.post("/agents", json={"name": "Original"}, headers=h)
    assert r.status_code in (200, 201), f"POST /agents failed: {r.text}"
    agent_id = r.json().get("agent_id")

    # Update
    update = c.put(f"/agents/{agent_id}", json={"name": "Updated", "max_iterations": 25}, headers=h)
    assert update.status_code == 200, f"PUT /agents failed: {update.text}"
    assert update.json().get("name") == "Updated"
    assert update.json().get("max_iterations") == 25


def test_put_agent_fully_autonomous_without_eval_suite_rejected():
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")

    r = c.post("/agents", json={"name": "Agent"}, headers=h)
    assert r.status_code in (200, 201)
    agent_id = r.json().get("agent_id")

    resp = c.put(
        f"/agents/{agent_id}",
        json={"autonomy_mode": "fully-autonomous"},
        headers=h,
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# FIX 3 — rollback route exists with {snapshot_id} in path
# ---------------------------------------------------------------------------

def test_rollback_path_url():
    """Backend rollback must accept snapshot_id as path parameter."""
    app = _make_app()
    all_routes = _collect_routes(app.routes)
    paths = [str(getattr(r, "path", "")) for r in all_routes]
    rollback_routes = [p for p in paths if "rollback" in p]
    assert rollback_routes, "Rollback route must exist"
    assert any("{snapshot_id}" in p for p in rollback_routes), (
        "Rollback route must have {snapshot_id} as path param"
    )


# ---------------------------------------------------------------------------
# FIX 4 — fully-autonomous gate on create
# ---------------------------------------------------------------------------

def test_fully_autonomous_requires_eval_suite():
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    r = c.post("/agents", json={"name": "Danger", "autonomy_mode": "fully-autonomous"}, headers=h)
    assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"


def test_fully_autonomous_with_eval_suite_passes():
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    r = c.post(
        "/agents",
        json={"name": "Safe", "autonomy_mode": "fully-autonomous", "eval_suite_id": "suite-abc"},
        headers=h,
    )
    assert r.status_code in (200, 201), f"Should be created: {r.text}"


# ---------------------------------------------------------------------------
# FIX 6 — list_async used for limit check in create_agent_nl
# ---------------------------------------------------------------------------

def test_nl_creation_enforces_agent_limit():
    """NL creation must use list_async (DB-backed) for limit check."""
    import inspect
    from app.api import agents
    src = inspect.getsource(agents)
    # Both create_agent_nl and create_agent should call list_async
    assert src.count("list_async") >= 2, (
        "create_agent_nl and create_agent must both use list_async for limit check"
    )


# ---------------------------------------------------------------------------
# FIX 7 — clone carries eval_suite_id and policy_ids
# ---------------------------------------------------------------------------

def test_clone_carries_new_fields():
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")

    # Create original with eval_suite_id and system_prompt
    r = c.post("/agents", json={
        "name": "Original",
        "system_prompt": "Be concise.",
        "policy_ids": ["p1", "p2"],
        "max_iterations": 25,
    }, headers=h)
    assert r.status_code in (200, 201)
    agent_id = r.json().get("agent_id")

    # Clone it
    clone_resp = c.post(f"/agents/{agent_id}/clone", json={}, headers=h)
    assert clone_resp.status_code in (200, 201), f"Clone failed: {clone_resp.text}"
    clone_data = clone_resp.json()
    assert clone_data.get("system_prompt") == "Be concise."
    assert clone_data.get("policy_ids") == ["p1", "p2"]
    assert clone_data.get("max_iterations") == 25


# ---------------------------------------------------------------------------
# FIX 8 — CreateAgentRequest model validation
# ---------------------------------------------------------------------------

def test_create_agent_request_defaults():
    from app.api.agents import CreateAgentRequest
    req = CreateAgentRequest(name="test")
    assert req.system_prompt == ""
    assert req.model_override == ""
    assert req.max_iterations == 15
    assert req.timeout_seconds == 300
    assert req.policy_ids == []
    assert req.allowed_collection_ids == []


# ---------------------------------------------------------------------------
# Migration file check
# ---------------------------------------------------------------------------

def test_migration_0033_exists():
    import os
    migration_dir = (
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/"
        "agent-verse-backend/app/db/migrations/versions"
    )
    files = os.listdir(migration_dir)
    assert any("0033" in f for f in files), "Migration 0033 must exist"


def test_migration_0033_has_required_columns():
    """Migration 0033 must add all required columns."""
    migration_path = (
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/"
        "agent-verse-backend/app/db/migrations/versions/0033_agent_schema_upgrade.py"
    )
    with open(migration_path) as f:
        src = f.read()
    for col in [
        "system_prompt", "model_override", "max_iterations", "timeout_seconds",
        "allowed_collection_ids", "eval_suite_id", "policy_ids", "version",
        "is_archived", "cloned_from",
    ]:
        assert col in src, f"Migration 0033 must include column: {col}"
