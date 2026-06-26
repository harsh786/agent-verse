"""Tests for /governance API endpoints."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.governance import router as governance_router
from app.governance.audit import AuditEvent, AuditLog
from app.governance.cost import BudgetConfig, CostController
from app.governance.hitl import HITLGateway
from app.governance.permissions import ActionLevel
from app.governance.policies import PolicyEngine
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-gov", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_govkey"


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
# Policies
# ---------------------------------------------------------------------------

def test_list_policies_empty() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/governance/policies", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_policy() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/governance/policies",
        json={
            "name": "no-delete-prod",
            "description": "Block all production delete ops",
            "tools_pattern": "delete_*",
            "action": "deny",
            "priority": 10,
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "no-delete-prod"
    assert body["action"] == "deny"
    assert "policy_id" in body

    # Verify it shows up in list.
    list_resp = client.get("/governance/policies", headers={"X-API-Key": _VALID_KEY})
    assert len(list_resp.json()) == 1


def test_delete_policy() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    create_resp = client.post(
        "/governance/policies",
        json={"name": "temp-policy", "tools_pattern": "temp_*", "action": "deny"},
        headers={"X-API-Key": _VALID_KEY},
    )
    policy_id = create_resp.json()["policy_id"]

    del_resp = client.delete(
        f"/governance/policies/{policy_id}", headers={"X-API-Key": _VALID_KEY}
    )
    assert del_resp.status_code == 204

    list_resp = client.get("/governance/policies", headers={"X-API-Key": _VALID_KEY})
    assert list_resp.json() == []


def test_delete_policy_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete(
        "/governance/policies/ghost-policy", headers={"X-API-Key": _VALID_KEY}
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# HITL approvals
# ---------------------------------------------------------------------------

def test_list_approvals_empty() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/governance/approvals", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_approve_request() -> None:
    gateway = HITLGateway()
    request_id = gateway.request_approval(
        goal_id="g-1",
        action="delete_database",
        risk_level="critical",
        tenant_ctx=_CTX,
    )
    client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)

    # Verify it's listed as pending.
    list_resp = client.get("/governance/approvals", headers={"X-API-Key": _VALID_KEY})
    assert len(list_resp.json()) == 1

    # Approve it.
    approve_resp = client.post(
        f"/governance/approvals/{request_id}/approve",
        json={"approver": "alice@example.com", "note": "Reviewed and confirmed"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert approve_resp.status_code == 200
    body = approve_resp.json()
    assert body["status"] == "approved"
    assert body["approver"] == "alice@example.com"

    # Should no longer appear in pending list.
    list_resp2 = client.get("/governance/approvals", headers={"X-API-Key": _VALID_KEY})
    assert list_resp2.json() == []


def test_reject_request() -> None:
    gateway = HITLGateway()
    request_id = gateway.request_approval(
        goal_id="g-2",
        action="send_mass_email",
        risk_level="high",
        tenant_ctx=_CTX,
    )
    client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)

    reject_resp = client.post(
        f"/governance/approvals/{request_id}/reject",
        json={"approver": "bob@example.com", "note": "Not authorised"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"


def test_approve_nonexistent_request() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/governance/approvals/ghost-req/approve",
        json={"approver": "alice"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def test_audit_log_empty() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/governance/audit", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_audit_log_query_filters() -> None:
    audit = AuditLog()
    audit.record(
        AuditEvent(
            goal_id="g-1",
            tool_name="write_file",
            action_level=ActionLevel.ALLOW_LOG,
            outcome="success",
        ),
        tenant_ctx=_CTX,
    )
    audit.record(
        AuditEvent(
            goal_id="g-2",
            tool_name="read_file",
            action_level=ActionLevel.ALLOW,
            outcome="success",
        ),
        tenant_ctx=_CTX,
    )
    client = TestClient(_make_app(audit=audit), raise_server_exceptions=False)

    # Filter by goal_id.
    resp = client.get(
        "/governance/audit?goal_id=g-1", headers={"X-API-Key": _VALID_KEY}
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["goal_id"] == "g-1"

    # Filter by tool_name.
    resp2 = client.get(
        "/governance/audit?tool_name=read_file", headers={"X-API-Key": _VALID_KEY}
    )
    assert len(resp2.json()) == 1
    assert resp2.json()[0]["tool_name"] == "read_file"


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

def test_budget_get_and_set() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)

    # Default budget values.
    get_resp = client.get("/governance/budget", headers={"X-API-Key": _VALID_KEY})
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["per_goal_usd"] == 10.0
    assert body["per_tenant_daily_usd"] == 500.0

    # Update budget.
    put_resp = client.put(
        "/governance/budget",
        json={"per_goal_usd": 25.0, "per_tenant_daily_usd": 1000.0},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert put_resp.status_code == 200
    updated = put_resp.json()
    assert updated["per_goal_usd"] == 25.0
    assert updated["per_tenant_daily_usd"] == 1000.0

    # Confirm persistence within same app instance.
    get_resp2 = client.get("/governance/budget", headers={"X-API-Key": _VALID_KEY})
    assert get_resp2.json()["per_goal_usd"] == 25.0


def test_governance_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    assert client.get("/governance/policies").status_code == 401
    assert client.get("/governance/approvals").status_code == 401
    assert client.get("/governance/audit").status_code == 401
    assert client.get("/governance/budget").status_code == 401
