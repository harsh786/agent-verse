"""Tests for P2.4 policy simulation endpoint."""
import pytest
from fastapi.testclient import TestClient


def test_policy_simulate_endpoint_exists():
    from app.main import create_app

    app = create_app()
    # Use OpenAPI spec for reliable route enumeration
    paths = list(app.openapi().get("paths", {}).keys())
    assert any(
        "simulate" in p for p in paths
    ), f"/governance/simulate endpoint must exist. Found simulate-related: {[p for p in paths if 'govern' in p]}"


def test_policy_simulate_returns_summary():
    from app.main import create_app

    app = create_app()
    client = TestClient(app)
    r = client.post(
        "/tenants/signup", json={"name": "SimTest", "email": "s@t.com"}
    )
    if r.status_code not in (200, 201):
        pytest.skip("signup failed")
    h = {"X-API-Key": r.json().get("api_key", "")}
    resp = client.post(
        "/governance/simulate",
        json={"goal": "search jira issues"},
        headers=h,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data
    assert "policy_checks" in data


def test_policy_simulate_with_default_tools():
    """Verify simulate returns known structure with default tool list."""
    from app.main import create_app

    app = create_app()
    client = TestClient(app)
    r = client.post(
        "/tenants/signup", json={"name": "SimTest2", "email": "s2@t.com"}
    )
    if r.status_code not in (200, 201):
        pytest.skip("signup failed")
    h = {"X-API-Key": r.json().get("api_key", "")}
    resp = client.post(
        "/governance/simulate",
        json={"goal": "deploy to production", "dry_run": True},
        headers=h,
    )
    assert resp.status_code == 200
    data = resp.json()
    summary = data["summary"]
    assert "allowed_tools" in summary
    assert "denied_tools" in summary
    assert "requires_approval" in summary
    assert "would_block_execution" in summary
    assert "hitl_approvals_needed" in summary
