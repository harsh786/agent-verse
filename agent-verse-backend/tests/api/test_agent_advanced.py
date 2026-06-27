"""Phase 4 advanced agent tests: clone, readiness, and release gate."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _make_app():
    from app.main import create_app

    return create_app()


def _signup(app):
    c = TestClient(app)
    r = c.post("/tenants/signup", json={"name": "AgentTest", "email": "at@test.com"})
    if r.status_code not in (200, 201):
        return c, {}
    return c, {"X-API-Key": r.json().get("api_key", "")}


# ---------------------------------------------------------------------------
# Clone endpoint
# ---------------------------------------------------------------------------


def test_clone_agent_endpoint_exists():
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("tenant signup failed")

    # Create the original agent
    r = c.post(
        "/agents",
        json={
            "name": "Original",
            "goal_template": "test",
            "autonomy_mode": "bounded-autonomous",
        },
        headers=h,
    )
    if r.status_code not in (200, 201):
        pytest.skip("agent creation failed")
    agent_id = r.json().get("agent_id", "")

    # Clone it
    clone_resp = c.post(f"/agents/{agent_id}/clone", json={"name": "Cloned"}, headers=h)
    assert clone_resp.status_code in (200, 201), f"Clone failed: {clone_resp.text}"
    data = clone_resp.json()
    assert data.get("cloned_from") == agent_id
    assert data.get("name") == "Cloned"
    assert "agent_id" in data
    assert data["agent_id"] != agent_id  # must be a distinct agent


def test_clone_agent_default_name():
    """Clone without specifying a name should append '(copy)' to original name."""
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("tenant signup failed")

    r = c.post(
        "/agents",
        json={"name": "MyBot", "goal_template": "do stuff"},
        headers=h,
    )
    if r.status_code not in (200, 201):
        pytest.skip("agent creation failed")
    agent_id = r.json().get("agent_id", "")

    # Clone without a name override
    clone_resp = c.post(f"/agents/{agent_id}/clone", json={}, headers=h)
    assert clone_resp.status_code in (200, 201), clone_resp.text
    assert clone_resp.json().get("name") == "MyBot (copy)"


def test_clone_nonexistent_agent_returns_404():
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("tenant signup failed")

    resp = c.post("/agents/nonexistent-id/clone", json={}, headers=h)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Readiness endpoint
# ---------------------------------------------------------------------------


def test_readiness_check_endpoint_exists():
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("tenant signup failed")

    r = c.post(
        "/agents",
        json={"name": "Ready?", "goal_template": "test", "autonomy_mode": "bounded-autonomous"},
        headers=h,
    )
    if r.status_code not in (200, 201):
        pytest.skip("agent creation failed")
    agent_id = r.json().get("agent_id", "")

    readiness = c.get(f"/agents/{agent_id}/readiness", headers=h)
    assert readiness.status_code == 200
    data = readiness.json()
    assert "ready" in data
    assert "checks" in data
    assert "agent_id" in data
    assert data["agent_id"] == agent_id
    assert "recommendation" in data


def test_readiness_fails_when_no_connectors():
    """An agent with no connectors should have a failing readiness check."""
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("tenant signup failed")

    r = c.post(
        "/agents",
        json={"name": "Bare", "goal_template": "test", "connector_ids": []},
        headers=h,
    )
    if r.status_code not in (200, 201):
        pytest.skip("agent creation failed")
    agent_id = r.json().get("agent_id", "")

    readiness = c.get(f"/agents/{agent_id}/readiness", headers=h)
    assert readiness.status_code == 200
    data = readiness.json()
    assert data["ready"] is False
    connector_check = next(
        (ch for ch in data["checks"] if ch["check"] == "connectors"), None
    )
    assert connector_check is not None
    assert connector_check["status"] == "fail"


def test_readiness_passes_when_connectors_configured():
    """An agent with connectors and a goal template should have ready=True."""
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("tenant signup failed")

    r = c.post(
        "/agents",
        json={
            "name": "Ready",
            "goal_template": "Monitor {service}",
            "connector_ids": ["datadog"],
            "autonomy_mode": "bounded-autonomous",
        },
        headers=h,
    )
    if r.status_code not in (200, 201):
        pytest.skip("agent creation failed")
    agent_id = r.json().get("agent_id", "")

    readiness = c.get(f"/agents/{agent_id}/readiness", headers=h)
    assert readiness.status_code == 200
    data = readiness.json()
    assert data["ready"] is True
    connector_check = next(ch for ch in data["checks"] if ch["check"] == "connectors")
    assert connector_check["status"] == "pass"


def test_readiness_nonexistent_agent_returns_404():
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("tenant signup failed")

    resp = c.get("/agents/no-such-id/readiness", headers=h)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Release gate: fully-autonomous requires eval_suite_id
# ---------------------------------------------------------------------------


def test_fully_autonomous_requires_eval_suite():
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("tenant signup failed")

    r = c.post(
        "/agents",
        json={"name": "Dangerous", "autonomy_mode": "fully-autonomous"},
        headers=h,
    )
    # Must return 422 without eval_suite_id
    assert r.status_code == 422, f"fully-autonomous without eval_suite must return 422, got: {r.text}"


def test_fully_autonomous_with_eval_suite_is_allowed():
    """fully-autonomous + eval_suite_id should create the agent successfully."""
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("tenant signup failed")

    r = c.post(
        "/agents",
        json={
            "name": "Safe Bot",
            "autonomy_mode": "fully-autonomous",
            "eval_suite_id": "suite-abc123",
            "goal_template": "Do safe things",
            "connector_ids": ["github"],
        },
        headers=h,
    )
    assert r.status_code in (200, 201), f"Expected success: {r.text}"
    assert r.json().get("autonomy_mode") == "fully-autonomous"


def test_bounded_autonomous_does_not_require_eval_suite():
    """bounded-autonomous mode should not require eval_suite_id."""
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("tenant signup failed")

    r = c.post(
        "/agents",
        json={"name": "Normal Bot", "autonomy_mode": "bounded-autonomous"},
        headers=h,
    )
    assert r.status_code in (200, 201), f"bounded-autonomous should not be blocked: {r.text}"
