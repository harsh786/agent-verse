"""Tests for additive governance SSE + channel-delete + legal-hold-list endpoints."""
from __future__ import annotations

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
    tenant_id="tid-stream", plan=PlanTier.PROFESSIONAL,
    api_key_id="kid-1", roles=("admin", "approver"),
)
_VALID_KEY = "av_test_streamkey"


def _make_app(hitl: HITLGateway | None = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(governance_router)
    app.state.hitl_gateway = hitl or HITLGateway()
    app.state.audit_log = AuditLog()
    app.state.cost_controller = CostController()
    app.state.policy_engine = PolicyEngine()
    # No Redis wired → snapshot + stream_unavailable path
    app.state._policy_pubsub_redis = None
    return app


def test_approvals_stream_emits_snapshot_without_redis() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    with client.stream(
        "GET", "/governance/approvals/stream", headers={"X-API-Key": _VALID_KEY}
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = ""
        for chunk in resp.iter_text():
            body += chunk
            if "stream_unavailable" in body:
                break
    assert "approvals_snapshot" in body
    assert "stream_unavailable" in body


def test_approvals_stream_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/governance/approvals/stream")
    assert resp.status_code == 401


def test_policies_stream_emits_snapshot_without_redis() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    with client.stream(
        "GET", "/governance/policies/stream", headers={"X-API-Key": _VALID_KEY}
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = ""
        for chunk in resp.iter_text():
            body += chunk
            if "stream_unavailable" in body:
                break
    assert "policies_snapshot" in body
    assert "stream_unavailable" in body


# ---------------------------------------------------------------------------
# DELETE /governance/notifications/{channel_id}
# ---------------------------------------------------------------------------

from app.services.notification_service import NotificationChannel, NotificationService  # noqa: E402


def _make_app_with_notifications() -> tuple[FastAPI, NotificationService]:
    app = _make_app()
    svc = NotificationService()
    app.state.notification_service = svc
    return app, svc


def test_delete_notification_channel() -> None:
    app, svc = _make_app_with_notifications()
    svc.add_channel(NotificationChannel(
        channel_id="c1", tenant_id=_CTX.tenant_id, channel_type="webhook", config={},
    ))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.delete("/governance/notifications/c1", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204
    assert svc.get_channels(_CTX.tenant_id) == []


def test_delete_missing_notification_channel_404() -> None:
    app, _ = _make_app_with_notifications()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.delete("/governance/notifications/nope", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /governance/legal-holds
# ---------------------------------------------------------------------------

def test_list_legal_holds_empty_without_db() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/governance/legal-holds", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []
