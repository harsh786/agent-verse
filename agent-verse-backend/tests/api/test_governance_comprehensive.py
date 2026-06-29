"""Comprehensive tests for /governance API endpoints — targets 20% → 55%+ coverage."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.governance import router as governance_router
from app.governance.audit import AuditLog
from app.governance.cost import BudgetConfig, CostController
from app.governance.hitl import HITLGateway
from app.governance.policies import PolicyEngine
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(
    tenant_id="tid-gov-comp",
    plan=PlanTier.PROFESSIONAL,
    api_key_id="kid-1",
    roles=("admin", "approver"),
)
_VALID_KEY = "av_test_gov_comp"


def _make_app(
    hitl: HITLGateway | None = None,
    audit: AuditLog | None = None,
    cost: CostController | None = None,
    policy_engine: PolicyEngine | None = None,
) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(governance_router)
    app.state.hitl_gateway = hitl or HITLGateway()
    app.state.audit_log = audit or AuditLog()
    app.state.cost_controller = cost or CostController()
    app.state.policy_engine = policy_engine or PolicyEngine()
    return app


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_list_policies_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/governance/policies")
    assert resp.status_code == 401


def test_get_budget_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/governance/budget")
    assert resp.status_code == 401


def test_list_approvals_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/governance/approvals")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------

def test_list_policies_empty() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/governance/policies", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_policy_deny() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/governance/policies",
        json={
            "name": "no-delete",
            "description": "Block deletes",
            "tools_pattern": "delete_*",
            "action": "deny",
            "priority": 10,
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "no-delete"
    assert body["action"] == "deny"
    assert "policy_id" in body


def test_create_policy_require_approval() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/governance/policies",
        json={
            "name": "prod-deploy-approval",
            "tools_pattern": "github.deploy",
            "action": "require_approval",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    assert resp.json()["action"] == "require_approval"


def test_create_policy_with_hours() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/governance/policies",
        json={
            "name": "biz-hours-only",
            "tools_pattern": "*",
            "action": "deny",
            "allowed_hours_utc": [9, 17],
            "allowed_weekdays": [0, 1, 2, 3, 4],
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["allowed_hours_utc"] == [9, 17]


def test_delete_policy_success() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    cr = client.post(
        "/governance/policies",
        json={"name": "to-delete", "tools_pattern": "noop.*", "action": "deny"},
        headers={"X-API-Key": _VALID_KEY},
    )
    policy_id = cr.json()["policy_id"]
    resp = client.delete(
        f"/governance/policies/{policy_id}",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 204
    # Confirm it's gone
    list_resp = client.get("/governance/policies", headers={"X-API-Key": _VALID_KEY})
    assert list_resp.json() == []


def test_delete_policy_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete(
        "/governance/policies/nonexistent",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


def test_simulate_policies() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/governance/policies/simulate",
        json={"tool_calls": ["github.read", "jira.delete"]},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "simulation_results" in body
    assert "github.read" in body["simulation_results"]


def test_simulate_goal_policies() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/governance/simulate",
        json={"goal": "Deploy to production", "dry_run": True},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "goal" in body
    assert "summary" in body


def test_simulate_goal_no_policy_engine() -> None:
    app = _make_app()
    app.state.policy_engine = None  # type: ignore
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/governance/simulate",
        json={"goal": "Deploy to production"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["summary"]["would_block_execution"] is False


# ---------------------------------------------------------------------------
# HITL approvals
# ---------------------------------------------------------------------------

def test_list_approvals_empty() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/governance/approvals", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_approvals_with_pending() -> None:
    hitl = HITLGateway()
    # Inject a pending request directly
    from app.governance.hitl import ApprovalRequest
    req = ApprovalRequest(
        request_id="req-1",
        goal_id="gid-1",
        action="github.deploy",
        risk_level="high",
    )
    hitl._requests[(_CTX.tenant_id, "req-1")] = req
    client = TestClient(_make_app(hitl=hitl), raise_server_exceptions=False)
    resp = client.get("/governance/approvals", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["request_id"] == "req-1"


def test_approve_request_success() -> None:
    hitl = HITLGateway()
    from app.governance.hitl import ApprovalRequest
    req = ApprovalRequest(
        request_id="req-approve",
        goal_id="gid-1",
        action="github.deploy",
        risk_level="high",
    )
    hitl._requests[(_CTX.tenant_id, "req-approve")] = req
    client = TestClient(_make_app(hitl=hitl), raise_server_exceptions=False)
    resp = client.post(
        "/governance/approvals/req-approve/approve",
        json={"approver": "ops-team", "note": "Looks good"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_approve_request_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/governance/approvals/nonexistent/approve",
        json={"approver": "ops"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


def test_reject_request_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/governance/approvals/nonexistent/reject",
        json={"approver": "ops"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Approval SSE streams
# ---------------------------------------------------------------------------

def test_stream_approvals_no_redis() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    with client.stream("GET", "/governance/approvals/stream", headers={"X-API-Key": _VALID_KEY}) as resp:
        assert resp.status_code == 200
        content = resp.read().decode()
    assert "approvals_snapshot" in content
    assert "stream_unavailable" in content


def test_stream_policies_no_redis() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    with client.stream("GET", "/governance/policies/stream", headers={"X-API-Key": _VALID_KEY}) as resp:
        assert resp.status_code == 200
        content = resp.read().decode()
    assert "policies_snapshot" in content
    assert "stream_unavailable" in content


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def test_query_audit_empty() -> None:
    audit = AuditLog()
    client = TestClient(_make_app(audit=audit), raise_server_exceptions=False)
    resp = client.get("/governance/audit", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_query_audit_with_entries() -> None:
    from app.governance.audit import AuditEvent
    from app.governance.permissions import ActionLevel

    audit = AuditLog()
    audit.record(
        AuditEvent(
            goal_id="gid-1",
            tool_name="github.read",
            action_level=ActionLevel.ALLOW,
            outcome="success",
            api_key_id="kid-1",
        ),
        tenant_ctx=_CTX,
    )
    client = TestClient(_make_app(audit=audit), raise_server_exceptions=False)
    resp = client.get("/governance/audit", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    assert entries[0]["tool_name"] == "github.read"


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

def test_get_budget_default() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/governance/budget", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert "per_goal_usd" in body
    assert "per_tenant_daily_usd" in body


def test_set_budget_success() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.put(
        "/governance/budget",
        json={"per_goal_usd": 5.0, "per_tenant_daily_usd": 200.0},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["per_goal_usd"] == 5.0
    assert body["per_tenant_daily_usd"] == 200.0


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def test_list_notifications_no_service() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/governance/notifications", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_notification_channel_no_service() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/governance/notifications",
        json={"channel_type": "webhook", "config": {"url": "https://hooks.example.com/abc"}},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 503


def test_create_notification_channel_with_service() -> None:
    from app.services.notification_service import NotificationService

    app = _make_app()
    svc = NotificationService()
    app.state.notification_service = svc
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/governance/notifications",
        json={"channel_type": "webhook", "config": {"url": "https://hooks.example.com/abc"}},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    assert resp.json()["type"] == "webhook"


def test_list_notifications_with_service() -> None:
    from app.services.notification_service import NotificationService

    app = _make_app()
    svc = NotificationService()
    app.state.notification_service = svc
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/governance/notifications", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Emergency stop
# ---------------------------------------------------------------------------

def test_emergency_stop_no_services() -> None:
    """Emergency stop should succeed even without optional services."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/governance/emergency-stop", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "emergency_stop_activated"
    assert "cancelled_goals" in body


def test_clear_emergency_stop() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete("/governance/emergency-stop", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["status"] == "cleared"


# ---------------------------------------------------------------------------
# Batch HITL approve
# ---------------------------------------------------------------------------

def test_batch_approve_empty() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/governance/hitl/batch-approve",
        json={"action": "approve", "request_ids": [], "approver": "ops"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approved"] == 0
    assert body["not_found"] == 0


def test_batch_approve_unknown_action() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/governance/hitl/batch-approve",
        json={"action": "bless", "request_ids": ["req-1"], "approver": "ops"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422


def test_batch_approve_exceeds_limit() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/governance/hitl/batch-approve",
        json={
            "action": "approve",
            "request_ids": [f"req-{i}" for i in range(101)],
            "approver": "ops",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422


def test_batch_approve_not_found_items() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/governance/hitl/batch-approve",
        json={"action": "approve", "request_ids": ["req-nonexistent"], "approver": "ops"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["not_found"] == 1


# ---------------------------------------------------------------------------
# Policy versions (no DB)
# ---------------------------------------------------------------------------

def _make_app_no_db(**kwargs) -> FastAPI:
    """App variant with db_session_factory explicitly disabled."""
    app = _make_app(**kwargs)
    app.state.db_session_factory = None
    return app


def test_get_policy_versions_no_db(monkeypatch) -> None:
    monkeypatch.setattr("app.api.governance.get_session_factory", lambda: None, raising=False)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: None, raising=False)
    app = _make_app_no_db()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/governance/policies/pol-1/versions", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_rollback_policy_no_db(monkeypatch) -> None:
    monkeypatch.setattr("app.api.governance.get_session_factory", lambda: None, raising=False)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: None, raising=False)
    app = _make_app_no_db()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/governance/policies/pol-1/rollback",
        json={"target_version": 1, "reason": "Fix regression"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code in (503, 500)


# ---------------------------------------------------------------------------
# Audit chain verify (no DB)
# ---------------------------------------------------------------------------

def test_verify_audit_chain_no_db(monkeypatch) -> None:
    monkeypatch.setattr("app.api.governance.get_session_factory", lambda: None, raising=False)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: None, raising=False)
    app = _make_app_no_db()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(
        "/governance/audit/integrity/verify",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code in (503, 500)


# ---------------------------------------------------------------------------
# SLA stats (no DB)
# ---------------------------------------------------------------------------

def test_sla_stats_no_db(monkeypatch) -> None:
    monkeypatch.setattr("app.api.governance.get_session_factory", lambda: None, raising=False)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: None, raising=False)
    app = _make_app_no_db()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/governance/approvals/sla-stats", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    # Should return either {"error": ...} or an empty/default stats dict
    body = resp.json()
    assert isinstance(body, dict)


# ---------------------------------------------------------------------------
# Legal holds (no DB)
# ---------------------------------------------------------------------------

def test_list_legal_holds_no_db(monkeypatch) -> None:
    monkeypatch.setattr("app.api.governance.get_session_factory", lambda: None, raising=False)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: None, raising=False)
    app = _make_app_no_db()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/governance/legal-holds", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_legal_hold_no_db(monkeypatch) -> None:
    monkeypatch.setattr("app.api.governance.get_session_factory", lambda: None, raising=False)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: None, raising=False)
    app = _make_app_no_db()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/governance/legal-hold",
        json={"reason": "Litigation hold"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code in (503, 500)
