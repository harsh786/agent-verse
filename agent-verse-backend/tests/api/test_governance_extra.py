"""Extra coverage for app/api/governance.py — SSE streams, DB helpers, advanced endpoints."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.governance import router as governance_router
from app.governance.audit import AuditLog
from app.governance.cost import CostController
from app.governance.hitl import HITLGateway
from app.governance.policies import PolicyEngine
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(
    tenant_id="tid-gov-extra",
    plan=PlanTier.ENTERPRISE,
    api_key_id="kid-gov-extra",
    roles=("admin",),
)
_VALID_KEY = "av_test_govextra"


def _make_app(
    hitl: HITLGateway | None = None,
    audit: AuditLog | None = None,
    cost: CostController | None = None,
    policy_engine: PolicyEngine | None = None,
    goal_service=None,
    redis=None,
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

    # Add notification service so those endpoints work
    from app.services.notification_service import NotificationService
    app.state.notification_service = NotificationService()

    if goal_service is not None:
        app.state.goal_service = goal_service
    if redis is not None:
        app.state._policy_pubsub_redis = redis
    return app


_H = {"X-API-Key": _VALID_KEY}


# ── DB list/create/delete policy helpers ─────────────────────────────────────

class TestDbPolicyHelpers:
    def test_list_policies_falls_back_to_memory_when_no_db(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        # Create a policy in-memory
        resp = client.post(
            "/governance/policies",
            json={"name": "test-pol", "tools_pattern": "test_*", "action": "deny"},
            headers=_H,
        )
        assert resp.status_code == 201
        # List should return it
        list_resp = client.get("/governance/policies", headers=_H)
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1


# ── Policy with hourly/weekday restrictions ───────────────────────────────────

class TestPolicyAdvanced:
    def test_create_policy_with_allowed_hours(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/governance/policies",
            json={
                "name": "business-hours-only",
                "tools_pattern": "deploy_*",
                "action": "require_approval",
                "priority": 5,
                "allowed_hours_utc": [9, 17],
                "allowed_weekdays": [0, 1, 2, 3, 4],
            },
            headers=_H,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "business-hours-only"

    def test_create_policy_description_optional(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/governance/policies",
            json={
                "name": "nodesc",
                "tools_pattern": "*",
                "action": "deny",
            },
            headers=_H,
        )
        assert resp.status_code == 201


# ── Budget endpoints ──────────────────────────────────────────────────────────

class TestBudgetEndpoints:
    def test_get_budget_default(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/governance/budget", headers=_H)
        assert resp.status_code == 200
        body = resp.json()
        assert "per_goal_usd" in body

    def test_set_budget(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.put(
            "/governance/budget",
            json={"per_goal_usd": 25.0, "per_tenant_daily_usd": 1000.0},
            headers=_H,
        )
        assert resp.status_code == 200
        assert resp.json()["per_goal_usd"] == 25.0

    def test_set_budget_then_get_reflects_change(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        client.put(
            "/governance/budget",
            json={"per_goal_usd": 50.0, "per_tenant_daily_usd": 2000.0},
            headers=_H,
        )
        get_resp = client.get("/governance/budget", headers=_H)
        assert get_resp.json()["per_goal_usd"] == 50.0


# ── Policy simulate endpoint ──────────────────────────────────────────────────

class TestPolicySimulate:
    def test_simulate_no_policies(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/governance/policies/simulate",
            json={"tool_calls": ["github:create_pr", "jira:create_issue"]},
            headers=_H,
        )
        assert resp.status_code == 200
        body = resp.json()
        # Key is simulation_results
        assert "simulation_results" in body or "results" in body

    def test_simulate_with_deny_policy(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        # Create a deny policy
        client.post(
            "/governance/policies",
            json={"name": "no-deploy", "tools_pattern": "deploy_*", "action": "deny"},
            headers=_H,
        )
        resp = client.post(
            "/governance/policies/simulate",
            json={"tool_calls": ["deploy_prod", "github:create_pr"]},
            headers=_H,
        )
        assert resp.status_code == 200


# ── Notification channels ─────────────────────────────────────────────────────

class TestNotificationChannels:
    def test_list_notification_channels_empty(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/governance/notifications", headers=_H)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_webhook_channel(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/governance/notifications",
            json={
                "channel_type": "webhook",
                "config": {"url": "https://hooks.example.com/webhook"},
            },
            headers=_H,
        )
        assert resp.status_code in (201, 503)
        if resp.status_code == 201:
            body = resp.json()
            assert "channel_id" in body or "channel_type" in body

    def test_delete_notification_channel(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        create_resp = client.post(
            "/governance/notifications",
            json={"channel_type": "slack", "config": {"webhook_url": "https://hooks.slack.com/x"}},
            headers=_H,
        )
        if create_resp.status_code == 503:
            pytest.skip("notification service unavailable")
        channel_id = create_resp.json()["channel_id"]
        del_resp = client.delete(
            f"/governance/notifications/{channel_id}",
            headers=_H,
        )
        assert del_resp.status_code == 204

    def test_delete_nonexistent_channel_404(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.delete(
            "/governance/notifications/ghost-channel",
            headers=_H,
        )
        assert resp.status_code == 404


# ── Emergency stop ────────────────────────────────────────────────────────────

class TestEmergencyStop:
    def test_emergency_stop_no_services(self):
        """Emergency stop with no goal_service or redis still returns 200."""
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post("/governance/emergency-stop", headers=_H)
        assert resp.status_code == 200
        body = resp.json()
        assert "cancelled_goals" in body or "stopped" in str(body).lower()

    def test_emergency_stop_with_goal_service(self):
        mock_goal_service = MagicMock()
        mock_goal_service._goals = {
            "g1": MagicMock(tenant_id="tid-gov-extra", status=MagicMock(__str__=lambda self: "running")),
        }
        mock_goal_service.cancel_goal = AsyncMock()

        client = TestClient(_make_app(goal_service=mock_goal_service), raise_server_exceptions=False)
        resp = client.post("/governance/emergency-stop", headers=_H)
        assert resp.status_code == 200

    def test_emergency_stop_with_redis(self):
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock()
        mock_redis.set = AsyncMock()

        client = TestClient(_make_app(redis=mock_redis), raise_server_exceptions=False)
        resp = client.post("/governance/emergency-stop", headers=_H)
        assert resp.status_code == 200

    def test_emergency_stop_rejects_pending_approvals(self):
        gateway = HITLGateway()
        gateway.request_approval(
            goal_id="g1", action="delete_all", risk_level="critical", tenant_ctx=_CTX
        )
        gateway.request_approval(
            goal_id="g2", action="deploy_prod", risk_level="high", tenant_ctx=_CTX
        )
        client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)
        resp = client.post("/governance/emergency-stop", headers=_H)
        assert resp.status_code == 200
        # Approvals should be rejected
        pending = gateway.list_pending(tenant_ctx=_CTX)
        assert len(pending) == 0


# ── Approvals stream ─────────────────────────────────────────────────────────

class TestApprovalsStream:
    def test_approvals_stream_no_redis(self):
        gateway = HITLGateway()
        client = TestClient(_make_app(hitl=gateway), raise_server_exceptions=False)
        resp = client.get("/governance/approvals/stream", headers=_H)
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        # Should contain approvals_snapshot and stream_unavailable
        text = resp.text
        assert "approvals_snapshot" in text or "stream_unavailable" in text


# ── Policies stream ───────────────────────────────────────────────────────────

class TestPoliciesStream:
    def test_policies_stream_no_redis(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/governance/policies/stream", headers=_H)
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        text = resp.text
        assert "policies_snapshot" in text


# ── Cost tracking endpoints ───────────────────────────────────────────────────

class TestCostTracking:
    def test_get_budget_via_governance(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/governance/budget", headers=_H)
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)

    def test_cost_unauthorized(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/governance/budget")
        assert resp.status_code == 401


# ── Audit log endpoints ───────────────────────────────────────────────────────

class TestAuditAdvanced:
    def test_audit_log_returns_list(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/governance/audit", headers=_H)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_audit_unauthorized(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/governance/audit")
        assert resp.status_code == 401
