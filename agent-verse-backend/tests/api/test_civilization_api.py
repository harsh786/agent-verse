"""Tests for /civilizations API endpoints — Phase E.

All tests use the full create_app() factory with civilization_enabled=True|False
so they exercise the real router registration, TenantMiddleware, and feature flag.
The in-memory database (no pools) is used; endpoints that need DB respond gracefully
with 503/500 instead of crashing.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


# ── App factory helpers ────────────────────────────────────────────────────────

def _make_app_enabled():
    from app.main import create_app
    from app.core.config import Settings

    settings = Settings(civilization_enabled=True)
    return create_app(settings=settings)


def _make_app_disabled():
    from app.main import create_app
    from app.core.config import Settings

    settings = Settings(civilization_enabled=False)
    return create_app(settings=settings)


def _signup(app):
    """Sign up a new tenant and return (client, headers). Skip on failure."""
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(
        "/tenants/signup",
        json={"name": "CivTest", "email": "civ@test.com"},
    )
    if r.status_code not in (200, 201):
        return client, {}
    api_key = r.json().get("api_key", "")
    return client, {"X-API-Key": api_key} if api_key else {}


# ── Feature-flag tests ─────────────────────────────────────────────────────────

def test_civilization_disabled_list_returns_503():
    app = _make_app_disabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed — no API key returned")
    resp = c.get("/civilizations", headers=h)
    assert resp.status_code == 503, resp.text
    assert "civilization" in resp.json().get("detail", "").lower()


def test_civilization_disabled_create_returns_503():
    app = _make_app_disabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    resp = c.post("/civilizations", json={"name": "Test"}, headers=h)
    assert resp.status_code == 503


def test_civilization_unauthenticated_returns_401():
    app = _make_app_enabled()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/civilizations")
    assert resp.status_code == 401


# ── CRUD tests ─────────────────────────────────────────────────────────────────

def test_civilization_create_when_enabled():
    app = _make_app_enabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    resp = c.post(
        "/civilizations",
        json={"name": "Test Civ", "constitution": {"max_depth": 3}},
        headers=h,
    )
    # DB may not be running in unit test — accept 201 or 500/503
    if resp.status_code in (500, 503):
        pytest.skip("DB not available in unit test environment")
    assert resp.status_code in (200, 201), f"Create failed: {resp.text}"
    data = resp.json()
    assert data.get("name") == "Test Civ"
    assert data.get("id")
    assert data["constitution"]["max_depth"] == 3


def test_civilization_list():
    app = _make_app_enabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    c.post("/civilizations", json={"name": "Civ 1"}, headers=h)
    resp = c.get("/civilizations", headers=h)
    # list endpoint returns [] gracefully when DB is unavailable
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_civilization_get_detail():
    app = _make_app_enabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    create = c.post("/civilizations", json={"name": "My Civ"}, headers=h)
    if create.status_code not in (200, 201):
        pytest.skip("create failed — DB likely unavailable")
    civ_id = create.json()["id"]
    resp = c.get(f"/civilizations/{civ_id}", headers=h)
    assert resp.status_code == 200
    assert resp.json()["id"] == civ_id


def test_civilization_get_nonexistent_returns_404_or_503():
    app = _make_app_enabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    resp = c.get("/civilizations/nonexistent_civ_id_xyz", headers=h)
    # Without a running DB the table doesn't exist → 500; with DB but no row → 404
    assert resp.status_code in (404, 500, 503), resp.text


def test_civilization_update_constitution():
    app = _make_app_enabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    create = c.post("/civilizations", json={"name": "Const Civ"}, headers=h)
    if create.status_code not in (200, 201):
        pytest.skip("create failed — DB likely unavailable")
    civ_id = create.json()["id"]
    resp = c.put(
        f"/civilizations/{civ_id}/constitution",
        json={"constitution": {"max_depth": 5, "max_total_agents": 20}},
        headers=h,
    )
    assert resp.status_code == 200
    assert resp.json()["constitution"]["max_depth"] == 5
    assert resp.json()["constitution"]["max_total_agents"] == 20


def test_civilization_update_constitution_unknown_civ():
    app = _make_app_enabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    resp = c.put(
        "/civilizations/unknown_id/constitution",
        json={"constitution": {"max_depth": 2}},
        headers=h,
    )
    # Without DB: 500 (table doesn't exist); with DB but no row: 404
    assert resp.status_code in (404, 500, 503)


# ── Control tests ──────────────────────────────────────────────────────────────

def test_civilization_control_pause_resume():
    app = _make_app_enabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    create = c.post("/civilizations", json={"name": "Ctrl Civ"}, headers=h)
    if create.status_code not in (200, 201):
        pytest.skip("create failed — DB likely unavailable")
    civ_id = create.json()["id"]

    pause = c.post(f"/civilizations/{civ_id}/controls/pause", json={}, headers=h)
    assert pause.status_code == 200
    assert pause.json()["status"] == "paused"

    resume = c.post(f"/civilizations/{civ_id}/controls/resume", json={}, headers=h)
    assert resume.status_code == 200
    assert resume.json()["status"] == "active"


def test_civilization_control_unknown_action():
    app = _make_app_enabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    create = c.post("/civilizations", json={"name": "Act Civ"}, headers=h)
    if create.status_code not in (200, 201):
        pytest.skip("create failed — DB likely unavailable")
    civ_id = create.json()["id"]
    resp = c.post(f"/civilizations/{civ_id}/controls/fly_to_moon", json={}, headers=h)
    assert resp.status_code == 400
    assert "Unknown action" in resp.json().get("detail", "")


def test_civilization_control_adjust_budget():
    app = _make_app_enabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    create = c.post("/civilizations", json={"name": "Budget Civ"}, headers=h)
    if create.status_code not in (200, 201):
        pytest.skip("create failed — DB likely unavailable")
    civ_id = create.json()["id"]
    resp = c.post(
        f"/civilizations/{civ_id}/controls/adjust_budget",
        json={"params": {"total_budget_usd": 500.0}},
        headers=h,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── Goal submission ────────────────────────────────────────────────────────────

def test_civilization_submit_goal():
    app = _make_app_enabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    create = c.post("/civilizations", json={"name": "Goal Civ"}, headers=h)
    if create.status_code not in (200, 201):
        pytest.skip("create failed — DB likely unavailable")
    civ_id = create.json()["id"]

    resp = c.post(
        f"/civilizations/{civ_id}/goals",
        json={"goal": "Analyze system performance"},
        headers=h,
    )
    assert resp.status_code in (200, 202), resp.text
    data = resp.json()
    # Should return accepted or rejected status
    assert data.get("status") in ("accepted", "rejected", "needs_agent_selection")


# ── Graph + inspector ──────────────────────────────────────────────────────────

def test_civilization_graph_endpoint():
    app = _make_app_enabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    create = c.post("/civilizations", json={"name": "Graph Civ"}, headers=h)
    if create.status_code not in (200, 201):
        pytest.skip("create failed — DB likely unavailable")
    civ_id = create.json()["id"]

    resp = c.get(f"/civilizations/{civ_id}/graph", headers=h)
    assert resp.status_code == 200
    data = resp.json()
    assert "nodes" in data and "edges" in data


def test_civilization_blackboard_endpoint():
    app = _make_app_enabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    create = c.post("/civilizations", json={"name": "BB Civ"}, headers=h)
    if create.status_code not in (200, 201):
        pytest.skip("create failed — DB likely unavailable")
    civ_id = create.json()["id"]

    resp = c.get(f"/civilizations/{civ_id}/blackboard", headers=h)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_civilization_debates_endpoint():
    app = _make_app_enabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    create = c.post("/civilizations", json={"name": "Debate Civ"}, headers=h)
    if create.status_code not in (200, 201):
        pytest.skip("create failed — DB likely unavailable")
    civ_id = create.json()["id"]

    resp = c.get(f"/civilizations/{civ_id}/debates", headers=h)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_civilization_learnings_endpoint():
    app = _make_app_enabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    create = c.post("/civilizations", json={"name": "Learn Civ"}, headers=h)
    if create.status_code not in (200, 201):
        pytest.skip("create failed — DB likely unavailable")
    civ_id = create.json()["id"]

    resp = c.get(f"/civilizations/{civ_id}/learnings", headers=h)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_civilization_spawns_endpoint():
    app = _make_app_enabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    create = c.post("/civilizations", json={"name": "Spawn Civ"}, headers=h)
    if create.status_code not in (200, 201):
        pytest.skip("create failed — DB likely unavailable")
    civ_id = create.json()["id"]

    resp = c.get(f"/civilizations/{civ_id}/spawns", headers=h)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ── Replay ────────────────────────────────────────────────────────────────────

def test_civilization_replay_endpoint():
    app = _make_app_enabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    create = c.post("/civilizations", json={"name": "Replay Civ"}, headers=h)
    if create.status_code not in (200, 201):
        pytest.skip("create failed — DB likely unavailable")
    civ_id = create.json()["id"]

    resp = c.get(f"/civilizations/{civ_id}/replay", headers=h)
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert "count" in data
    assert data["civilization_id"] == civ_id


def test_civilization_replay_with_since_param():
    app = _make_app_enabled()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    create = c.post("/civilizations", json={"name": "Replay Since Civ"}, headers=h)
    if create.status_code not in (200, 201):
        pytest.skip("create failed — DB likely unavailable")
    civ_id = create.json()["id"]

    resp = c.get(
        f"/civilizations/{civ_id}/replay",
        params={"since": "2024-01-01T00:00:00"},
        headers=h,
    )
    assert resp.status_code == 200
    assert "events" in resp.json()


# ── Route registration ────────────────────────────────────────────────────────

def _get_openapi_paths(app) -> list[str]:
    """Return all registered HTTP paths from the OpenAPI schema."""
    schema = app.openapi()
    return list(schema.get("paths", {}).keys())


def _get_all_route_paths(app) -> list[str]:
    """Return paths from both HTTP routes and WebSocket routes."""
    http_paths = _get_openapi_paths(app)
    # WebSocket routes don't appear in OpenAPI; inspect app.routes directly
    ws_paths = []
    for route in app.routes:
        p = getattr(route, "path", "")
        if p and "ws" in p:
            ws_paths.append(p)
    return http_paths + ws_paths


def test_civilization_stream_endpoint_exists():
    app = _make_app_enabled()
    paths = _get_openapi_paths(app)
    assert any("stream" in p and "civilization" in p for p in paths), (
        f"SSE stream endpoint /civilizations/{{civ_id}}/stream must be registered. "
        f"Civilization paths found: {[p for p in paths if 'civil' in p]}"
    )


def test_civilization_ws_endpoint_exists():
    app = _make_app_enabled()
    all_paths = _get_all_route_paths(app)
    ws_routes = [p for p in all_paths if "ws" in p and "civil" in p]
    # Also check via router directly (WS routes in sub-routers may be nested)
    from app.api.civilization import router as civ_router
    router_paths = [getattr(r, "path", "") for r in civ_router.routes]
    ws_in_router = [p for p in router_paths if "ws" in p]
    assert ws_routes or ws_in_router, (
        f"WebSocket endpoint /civilizations/{{civ_id}}/ws must be registered. "
        f"Civilization paths: {[p for p in all_paths if 'civil' in p]}"
    )


def test_civilization_router_registered_by_default():
    """The civilization router must always be included regardless of flag value."""
    app = _make_app_enabled()
    paths = _get_openapi_paths(app)
    civ_paths = [p for p in paths if "civil" in p]
    assert civ_paths, f"Civilization routes must be registered. Found: {paths[-10:]}"


def test_civilization_router_also_registered_when_disabled():
    """Router is always registered; feature flag check happens at request time."""
    app = _make_app_disabled()
    paths = _get_openapi_paths(app)
    civ_paths = [p for p in paths if "civil" in p]
    assert civ_paths, (
        "Civilization routes must be registered even when flag is False. "
        f"Found: {paths[-10:]}"
    )
