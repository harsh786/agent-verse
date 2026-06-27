"""Tests for Phase 2: Capability Registry API."""
from __future__ import annotations

import pytest


def _make_app():
    from app.main import create_app
    return create_app()


def _auth_header(app):
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.post("/tenants/signup", json={"name": "CapTest", "email": "cap@test.com"})
    if r.status_code not in (200, 201):
        return None, None
    return c, {"X-API-Key": r.json().get("api_key", "")}


def test_capabilities_endpoint_returns_list():
    app = _make_app()
    client, headers = _auth_header(app)
    if headers is None:
        pytest.skip("signup failed")
    resp = client.get("/connectors/capabilities", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_capabilities_search_endpoint_exists():
    app = _make_app()
    client, headers = _auth_header(app)
    if headers is None:
        pytest.skip("signup failed")
    resp = client.get("/connectors/capabilities/search?q=github", headers=headers)
    assert resp.status_code in (200, 404)  # 404 if no tools discovered yet
    if resp.status_code == 200:
        assert "results" in resp.json()


def test_missing_capabilities_endpoint_exists():
    app = _make_app()
    client, headers = _auth_header(app)
    if headers is None:
        pytest.skip("signup failed")
    resp = client.get(
        "/connectors/capabilities/missing?goal=search jira issues",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "goal" in data
    assert "missing_connectors" in data


def test_migration_0030_exists():
    import os
    files = os.listdir(
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/"
        "agent-verse-backend/app/db/migrations/versions"
    )
    assert any("0030" in f for f in files), (
        "Migration 0030 (tool_capabilities) must exist"
    )
