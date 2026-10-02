"""Regression: HITL email approval links were forgeable and never expired.

* The HMAC key fell back to the public ``changeme-please-set-HITL_EMAIL_SECRET``
  in every environment (production included).
* The signed payload was only ``request_id:action`` -- no expiry, no tenant.
* The endpoint decided as a synthetic ``email-link`` principal with no
  approver-role check, in whatever tenant owned the request.
* Notification links were built with ``?token=`` while the endpoint reads
  ``?sig=`` -- every notification click answered 403.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.governance import router as governance_router
from app.governance.hitl import HITLGateway
from app.integrations.email import approval_sender as sender
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

TENANT = "t-email-link"
OTHER = "t-other"

APPROVER = TenantContext(
    tenant_id=TENANT, plan=PlanTier.ENTERPRISE, api_key_id="kid-alice", roles=("approver",)
)
VIEWER = TenantContext(
    tenant_id=TENANT, plan=PlanTier.ENTERPRISE, api_key_id="kid-view", roles=("viewer",)
)
OTHER_APPROVER = TenantContext(
    tenant_id=OTHER, plan=PlanTier.ENTERPRISE, api_key_id="kid-mallory", roles=("approver",)
)
_KEYS = {"k-approver": APPROVER, "k-viewer": VIEWER, "k-other": OTHER_APPROVER}


def _app(gateway: HITLGateway) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _KEYS.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(governance_router)
    app.state.hitl_gateway = gateway
    return app


def _pending(gateway: HITLGateway) -> str:
    return str(gateway.request_approval(goal_id="g", action="deploy prod", tenant_ctx=APPROVER))


# ---------------------------------------------------------------------------
# Signing secret
# ---------------------------------------------------------------------------


def test_production_refuses_default_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("HITL_EMAIL_SECRET", raising=False)
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)
    with pytest.raises(RuntimeError, match="HITL_EMAIL_SECRET"):
        sender.signed_link_query("r1", "approve", tenant_id=TENANT)
    # Verification fails closed rather than raising into the endpoint.
    assert sender._verify("r1", "approve", "x" * 64, tenant_id=TENANT, exp=2**31) is False


def test_production_derives_secret_from_vault_master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("HITL_EMAIL_SECRET", raising=False)
    monkeypatch.setenv("VAULT_MASTER_KEY", "master-key-material")
    secret = sender._signing_secret()
    assert secret and "changeme" not in secret and secret != sender._DEV_SECRET


def test_old_public_default_secret_cannot_forge(monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib
    import hmac

    monkeypatch.delenv("HITL_EMAIL_SECRET", raising=False)
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)
    forged = hmac.new(
        b"changeme-please-set-HITL_EMAIL_SECRET", b"r1:approve", hashlib.sha256
    ).hexdigest()[:32]
    assert sender._verify("r1", "approve", forged, tenant_id=TENANT, exp=2**31) is False


# ---------------------------------------------------------------------------
# Payload binding + expiry
# ---------------------------------------------------------------------------


def test_signature_is_bound_to_tenant_action_and_expiry() -> None:
    exp = int(time.time()) + 60
    sig = sender._sign("r1", "approve", tenant_id=TENANT, exp=exp)
    assert sender._verify("r1", "approve", sig, tenant_id=TENANT, exp=exp)
    assert not sender._verify("r1", "approve", sig, tenant_id=OTHER, exp=exp)
    assert not sender._verify("r1", "reject", sig, tenant_id=TENANT, exp=exp)
    assert not sender._verify("r2", "approve", sig, tenant_id=TENANT, exp=exp)
    assert not sender._verify("r1", "approve", sig, tenant_id=TENANT, exp=exp + 1)


def test_expired_link_is_rejected() -> None:
    exp = int(time.time()) - 1
    sig = sender._sign("r1", "approve", tenant_id=TENANT, exp=exp)
    assert not sender._verify("r1", "approve", sig, tenant_id=TENANT, exp=exp)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


def test_endpoint_attributes_decision_to_authenticated_approver() -> None:
    gw = HITLGateway()
    rid = _pending(gw)
    q = sender.signed_link_query(rid, "approve", tenant_id=TENANT)
    resp = TestClient(_app(gw)).post(
        f"/governance/hitl/{rid}/approve?{q}", headers={"X-API-Key": "k-approver"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["approver"] == "kid-alice"
    assert gw.get_request(rid, tenant_ctx=APPROVER).approver == "kid-alice"


def test_endpoint_requires_approver_role() -> None:
    gw = HITLGateway()
    rid = _pending(gw)
    q = sender.signed_link_query(rid, "approve", tenant_id=TENANT)
    resp = TestClient(_app(gw)).post(
        f"/governance/hitl/{rid}/approve?{q}", headers={"X-API-Key": "k-viewer"}
    )
    assert resp.status_code == 403
    assert gw.get_request(rid, tenant_ctx=APPROVER).status.value == "pending"


def test_endpoint_rejects_link_for_another_tenant() -> None:
    gw = HITLGateway()
    rid = _pending(gw)
    q = sender.signed_link_query(rid, "approve", tenant_id=TENANT)
    resp = TestClient(_app(gw)).post(
        f"/governance/hitl/{rid}/approve?{q}", headers={"X-API-Key": "k-other"}
    )
    assert resp.status_code == 403
    assert gw.get_request(rid, tenant_ctx=APPROVER).status.value == "pending"


def test_endpoint_rejects_expired_link() -> None:
    gw = HITLGateway()
    rid = _pending(gw)
    q = sender.signed_link_query(rid, "reject", tenant_id=TENANT, ttl_s=-5)
    resp = TestClient(_app(gw)).post(
        f"/governance/hitl/{rid}/reject?{q}", headers={"X-API-Key": "k-approver"}
    )
    assert resp.status_code == 403
    assert gw.get_request(rid, tenant_ctx=APPROVER).status.value == "pending"


def test_endpoint_rejects_legacy_sig_without_exp() -> None:
    gw = HITLGateway()
    rid = _pending(gw)
    sig = sender._sign(rid, "approve", tenant_id=TENANT, exp=int(time.time()) + 60)
    resp = TestClient(_app(gw)).post(
        f"/governance/hitl/{rid}/approve?sig={sig}", headers={"X-API-Key": "k-approver"}
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Notification links use the same verifiable format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notification_links_are_signed_and_verifiable() -> None:
    from app.services.notification_service import NotificationService

    svc = NotificationService()
    sent: list[dict[str, Any]] = []
    svc.ensure_tenant_loaded = AsyncMock()  # type: ignore[method-assign]
    svc.get_channels = lambda tenant_id: [type("C", (), {"channel_id": "c1"})()]  # type: ignore[method-assign]

    async def _send(channel: Any, message: dict[str, Any]) -> None:
        sent.append(message)

    svc._send = _send  # type: ignore[method-assign]
    await svc.notify_approval_required(
        request_id="r-n", goal_id="g", action="deploy", risk_level="high", tenant_id=TENANT
    )

    assert sent
    for key, action in (("approve_url", "approve"), ("reject_url", "reject")):
        url = urlparse(sent[0][key])
        assert "token=" not in url.query
        qs = parse_qs(url.query)
        assert url.path.endswith(f"/hitl/r-n/{action}")
        assert sender._verify(
            "r-n", action, qs["sig"][0], tenant_id=TENANT, exp=int(qs["exp"][0])
        )
