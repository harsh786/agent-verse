"""Tests for workflow HITL router (approval inbox, decide, delegate, escalate)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.workflow.hitl_extension import WorkflowHITLRequest


def _req(request_id: str = "req-1") -> WorkflowHITLRequest:
    return WorkflowHITLRequest(
        request_id=request_id,
        run_id="run-1",
        workflow_id="wf-1",
        tenant_id="test-tenant",
        step_id="review",
        priority="medium",
        status="pending",
    )


def make_app(gateway: MagicMock) -> TestClient:
    from fastapi import FastAPI, Request

    from app.tenancy.context import PlanLimits, PlanTier, TenantContext
    from app.workflow.router_hitl import router

    app = FastAPI()

    class FakeTenant(TenantContext):
        def __init__(self) -> None:
            pass
        tenant_id = "test-tenant"
        plan = PlanTier.FREE
        api_key = "test-key"
        api_key_id = "key-1"
        limits = PlanLimits(60, 25, 3, 2, 1, 3600)

    @app.middleware("http")
    async def inject_state(request: Request, call_next):
        request.app.state.hitl_workflow_gateway = gateway
        request.app.state.tenant_context = FakeTenant()
        request.app.state.current_user_id = "user-1"
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def gateway() -> MagicMock:
    gw = AsyncMock()
    req = _req()
    gw.list_pending.return_value = ([req], 1)
    gw.get_request.return_value = req
    gw.decide.return_value = WorkflowHITLRequest(
        request_id="req-1", run_id="run-1", workflow_id="wf-1",
        tenant_id="test-tenant", step_id="review",
        status="approved", action_taken="approved",
        reviewed_by="user-1", reviewed_at="2026-01-01T00:00:00Z",
    )
    gw.delegate.return_value = WorkflowHITLRequest(
        request_id="req-1", run_id="run-1", workflow_id="wf-1",
        tenant_id="test-tenant", step_id="review",
        status="pending", assigned_to="user-2",
        discussion=[{"type": "delegation", "from": "user-1", "to": "user-2"}],
    )
    gw.escalate.return_value = WorkflowHITLRequest(
        request_id="req-1", run_id="run-1", workflow_id="wf-1",
        tenant_id="test-tenant", step_id="review",
        status="pending",
        discussion=[{"type": "escalation", "by": "user-1"}],
    )
    gw.bulk_decide.return_value = [_req("req-1"), _req("req-2")]
    gw.consume_magic_link.return_value = {"request_id": "req-1", "action": "approve"}
    gw.get_stats.return_value = {
        "pending_count": 1, "total_requests": 5, "avg_resolution_seconds": 300.0
    }
    return gw


@pytest.fixture
def client(gateway: MagicMock) -> TestClient:
    return make_app(gateway)


# ── List approvals ────────────────────────────────────────────────────────────


def test_list_approvals(client: TestClient) -> None:
    resp = client.get("/api/v1/approvals")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["total"] == 1


def test_list_approvals_with_priority_filter(client: TestClient) -> None:
    resp = client.get("/api/v1/approvals?priority=critical")
    assert resp.status_code == 200


# ── Stats ─────────────────────────────────────────────────────────────────────


def test_approval_stats(client: TestClient) -> None:
    resp = client.get("/api/v1/approvals/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "pending_count" in data
    assert data["pending_count"] == 1


# ── Get approval ──────────────────────────────────────────────────────────────


def test_get_approval(client: TestClient) -> None:
    resp = client.get("/api/v1/approvals/req-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == "req-1"


def test_get_approval_not_found(client: TestClient, gateway: MagicMock) -> None:
    gateway.get_request.return_value = None
    resp = client.get("/api/v1/approvals/bad-req")
    assert resp.status_code == 404


# ── Decide ────────────────────────────────────────────────────────────────────


def test_decide_approve(client: TestClient) -> None:
    resp = client.post("/api/v1/approvals/req-1/decide", json={
        "action": "approved", "note": "Looks good"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "approved"
    assert data["reviewed_by"] == "user-1"


def test_decide_reject(client: TestClient, gateway: MagicMock) -> None:
    gateway.decide.return_value = WorkflowHITLRequest(
        request_id="req-1", run_id="run-1", workflow_id="wf-1",
        tenant_id="test-tenant", step_id="review",
        status="rejected", action_taken="rejected", reviewed_by="user-1",
    )
    resp = client.post("/api/v1/approvals/req-1/decide", json={"action": "rejected"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"


def test_decide_not_found(client: TestClient, gateway: MagicMock) -> None:
    gateway.decide.side_effect = ValueError("not found")
    resp = client.post("/api/v1/approvals/bad/decide", json={"action": "approved"})
    assert resp.status_code == 404


# ── Delegate ──────────────────────────────────────────────────────────────────


def test_delegate(client: TestClient) -> None:
    resp = client.post("/api/v1/approvals/req-1/delegate", json={
        "to_user_id": "user-2", "note": "OOO"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["assigned_to"] == "user-2"


def test_delegate_non_pending(client: TestClient, gateway: MagicMock) -> None:
    gateway.delegate.side_effect = ValueError("Cannot delegate a non-pending request")
    resp = client.post("/api/v1/approvals/req-1/delegate", json={"to_user_id": "u"})
    assert resp.status_code == 409


# ── Escalate ──────────────────────────────────────────────────────────────────


def test_escalate(client: TestClient) -> None:
    resp = client.post("/api/v1/approvals/req-1/escalate")
    assert resp.status_code == 200
    data = resp.json()
    assert any(c["type"] == "escalation" for c in data.get("discussion", []))


# ── Bulk decide ───────────────────────────────────────────────────────────────


def test_bulk_decide(client: TestClient) -> None:
    resp = client.post("/api/v1/approvals/bulk-decide", json={
        "request_ids": ["req-1", "req-2"],
        "action": "approved",
        "note": "Bulk approval",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["decided"] == 2


# ── Magic link ────────────────────────────────────────────────────────────────


def test_magic_link_valid(client: TestClient, gateway: MagicMock) -> None:
    gateway.decide.return_value = _req("req-1")
    gateway.decide.return_value.status = "approved"
    resp = client.get("/api/v1/approvals/magic/tok123?action=approve")
    assert resp.status_code == 200


def test_magic_link_expired(client: TestClient, gateway: MagicMock) -> None:
    gateway.consume_magic_link.return_value = None
    resp = client.get("/api/v1/approvals/magic/bad-token?action=approve")
    assert resp.status_code == 410
