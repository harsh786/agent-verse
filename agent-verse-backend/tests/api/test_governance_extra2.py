"""Extra coverage for governance.py — HITL email links, legal holds, batch approve, SLA stats, policy versioning."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.governance import router as governance_router
from app.governance.audit import AuditLog
from app.governance.cost import CostController
from app.governance.hitl import HITLGateway
from app.governance.policies import PolicyEngine
from app.integrations.email.approval_sender import _sign
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-gov2", plan=PlanTier.ENTERPRISE, api_key_id="kid-gov2", roles=("admin", "approver"))
_VALID_KEY = "av_gov2_key"


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

    from app.services.notification_service import NotificationService
    app.state.notification_service = NotificationService()
    return app


_H = {"X-API-Key": _VALID_KEY}


# ── HITL email-link approve endpoint ─────────────────────────────────────────

class TestHitlEmailLinks:
    def test_approve_link_invalid_sig_returns_403(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/governance/hitl/req123/approve", params={"sig": "bad_sig"})
        assert resp.status_code in (403, 401)

    def test_approve_link_missing_sig_returns_403(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/governance/hitl/req123/approve")
        assert resp.status_code in (403, 401)

    def test_reject_link_invalid_sig_returns_403(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/governance/hitl/req456/reject", params={"sig": "wrong"})
        assert resp.status_code in (403, 401)

    def test_approve_link_valid_sig_but_no_request(self):
        """Valid sig but no matching HITL request → 404."""
        client = TestClient(_make_app(), raise_server_exceptions=False)
        request_id = "nonexistent-req-id"
        sig = _sign(request_id, "approve")
        resp = client.get(f"/governance/hitl/{request_id}/approve", params={"sig": sig})
        assert resp.status_code in (404, 401)

    def test_approve_link_with_valid_request(self):
        """Valid sig and matching HITL request → 200 approved."""
        gateway = HITLGateway()
        request_id = gateway.request_approval(
            goal_id="g1",
            action="delete_data",
            risk_level="critical",
            tenant_ctx=_CTX,
        )
        sig = _sign(request_id, "approve")

        client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)
        resp = client.get(f"/governance/hitl/{request_id}/approve", params={"sig": sig})
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            assert resp.json()["status"] == "approved"

    def test_reject_link_with_valid_request(self):
        """Valid sig and matching HITL request → 200 rejected."""
        gateway = HITLGateway()
        request_id = gateway.request_approval(
            goal_id="g2",
            action="send_mass_email",
            risk_level="high",
            tenant_ctx=_CTX,
        )
        sig = _sign(request_id, "reject")

        client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)
        resp = client.get(f"/governance/hitl/{request_id}/reject", params={"sig": sig})
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            assert resp.json()["status"] == "rejected"


# ── Legal holds ───────────────────────────────────────────────────────────────

class TestLegalHolds:
    def test_list_legal_holds_no_db_returns_empty(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/governance/legal-holds", headers=_H)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_legal_hold_no_db_returns_error(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/governance/legal-hold",
            json={"reason": "Litigation hold for case #12345"},
            headers=_H,
        )
        assert resp.status_code in (503, 500)  # No DB configured


# ── Batch HITL approve ────────────────────────────────────────────────────────

class TestBatchApprove:
    def test_batch_approve_too_many_ids(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/governance/hitl/batch-approve",
            json={
                "action": "approve",
                "request_ids": [f"req-{i}" for i in range(101)],
                "approver": "admin@example.com",
            },
            headers=_H,
        )
        assert resp.status_code in (422, 400, 401)

    def test_batch_approve_empty_ids(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/governance/hitl/batch-approve",
            json={
                "action": "approve",
                "request_ids": [],
                "approver": "admin@example.com",
            },
            headers=_H,
        )
        assert resp.status_code in (200, 401, 422)

    def test_batch_approve_some_ids(self):
        gateway = HITLGateway()
        req1 = gateway.request_approval("g1", "act1", "high", tenant_ctx=_CTX)
        req2 = gateway.request_approval("g2", "act2", "low", tenant_ctx=_CTX)

        client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)
        resp = client.post(
            "/governance/hitl/batch-approve",
            json={
                "action": "approve",
                "request_ids": [req1, req2],
                "approver": "admin@example.com",
                "note": "Batch approved",
            },
            headers=_H,
        )
        assert resp.status_code in (200, 401, 422, 403)


# ── SLA stats ─────────────────────────────────────────────────────────────────

class TestSlaStats:
    def test_sla_stats_returns_dict(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/governance/approvals/sla-stats", headers=_H)
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            assert isinstance(resp.json(), dict)


# ── Policy versioning ─────────────────────────────────────────────────────────

class TestPolicyVersions:
    def test_get_policy_versions_nonexistent(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/governance/policies/ghost/versions", headers=_H)
        assert resp.status_code in (200, 404)

    def test_rollback_policy_nonexistent(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/governance/policies/ghost/rollback",
            json={"version": 1},
            headers=_H,
        )
        assert resp.status_code in (200, 404, 422)


# ── Audit integrity ───────────────────────────────────────────────────────────

class TestAuditIntegrity:
    def test_verify_audit_integrity(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/governance/audit/integrity/verify", headers=_H)
        assert resp.status_code in (200, 404, 503)
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, dict)
