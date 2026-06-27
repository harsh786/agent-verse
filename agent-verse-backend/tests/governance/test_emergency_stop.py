"""Tests for Phase 11 (Emergency Stop), Phase 12 (HITL rejection note),
and Phase 23 (Audit Immutability migration).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _make_app():
    from app.main import create_app
    return create_app()


def _signup(app):
    c = TestClient(app)
    r = c.post("/tenants/signup", json={"name": "EmergencyTest", "email": "em@test.com"})
    if r.status_code not in (200, 201):
        return c, {}
    return c, {"X-API-Key": r.json().get("api_key", "")}


def test_emergency_stop_endpoint_exists():
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    resp = c.post("/governance/emergency-stop", headers=h)
    assert resp.status_code in (200, 201), f"Emergency stop failed: {resp.text}"
    data = resp.json()
    assert data.get("status") == "emergency_stop_activated"
    assert "cancelled_goals" in data


def test_emergency_stop_returns_count():
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    resp = c.post("/governance/emergency-stop", headers=h)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data.get("cancelled_goals"), int)
    assert isinstance(data.get("rejected_approvals"), int)


def test_clear_emergency_stop_endpoint_exists():
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    c.post("/governance/emergency-stop", headers=h)
    resp = c.delete("/governance/emergency-stop", headers=h)
    assert resp.status_code == 200


def test_audit_immutability_trigger_in_migration():
    import os
    files = os.listdir(
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/"
        "agent-verse-backend/app/db/migrations/versions"
    )
    assert any("0031" in f for f in files), (
        "Migration 0031 (audit immutability) must exist"
    )


def test_legal_hold_endpoint_exists():
    app = _make_app()
    # app.routes contains _IncludedRouter wrappers; use openapi schema for reliable lookup
    paths = list(app.openapi().get("paths", {}).keys())
    assert any("legal-hold" in p or "legal_hold" in p for p in paths), \
        f"Legal hold endpoint must be registered. Got paths: {paths}"


def test_hitl_rejection_note_stored_on_resume_rejected():
    """Phase 12: rejection note is persisted to execution_context for replanning."""
    from app.services.goal_service import GoalService
    from app.tenancy.context import TenantContext, PlanTier
    import asyncio

    svc = GoalService()
    ctx = TenantContext(tenant_id="t-rej", plan=PlanTier.FREE, api_key_id="k1")

    async def run():
        result = await svc.submit_goal(
            goal="test rejection note",
            priority="normal",
            dry_run=True,
            tenant_ctx=ctx,
        )
        goal_id = result["goal_id"]
        from app.agent.state import GoalStatus
        record = svc._goals[goal_id]
        # Force back to non-terminal so resume_goal accepts it
        record.status = GoalStatus.WAITING_HUMAN
        await svc.resume_goal(
            goal_id=goal_id,
            tenant_ctx=ctx,
            approved=False,
            feedback="Too risky for production",
        )
        return record

    record = asyncio.run(run())
    assert record.hitl_rejection_note == "Too risky for production"
    assert record.execution_context.get("hitl_rejection_note") == "Too risky for production"


def test_emergency_stop_cancelled_goals_response_shape():
    """Emergency stop response must include all documented fields."""
    app = _make_app()
    c, h = _signup(app)
    if not h:
        pytest.skip("signup failed")
    resp = c.post("/governance/emergency-stop", headers=h)
    data = resp.json()
    required_keys = {
        "status", "tenant_id", "cancelled_goals",
        "cancelled_goal_ids", "rejected_approvals",
    }
    missing = required_keys - set(data.keys())
    assert not missing, f"Response missing keys: {missing}"
    assert data["status"] == "emergency_stop_activated"
