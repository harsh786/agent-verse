"""QA-5: ``POST /governance/notifications`` validates and normalizes channels.

Creation took a free-form ``channel_type`` / ``config``, so a channel that could
never deliver (unknown type, no URL, Teams saved under ``webhook_url``) was
reported "created" and only failed later, silently, when an approval needed it.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.governance import router as governance_router
from app.services.notification_service import NotificationService
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="t-qa5", plan=PlanTier.PROFESSIONAL, api_key_id="k-qa5")
_KEY = "av_test_qa5"
_HDR = {"X-API-Key": _KEY}


def _client() -> tuple[TestClient, NotificationService]:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(governance_router)
    svc = NotificationService()
    app.state.notification_service = svc
    return TestClient(app, raise_server_exceptions=False), svc


def test_teams_channel_with_webhook_url_is_stored_under_url() -> None:
    client, svc = _client()

    resp = client.post(
        "/governance/notifications",
        json={"channel_type": "teams",
              "config": {"webhook_url": "https://contoso.webhook.office.com/abc"}},
        headers=_HDR,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["type"] == "teams"
    [channel] = svc.get_channels(_CTX.tenant_id)
    assert channel.config == {"url": "https://contoso.webhook.office.com/abc"}


def test_unsupported_channel_type_is_422() -> None:
    client, svc = _client()

    resp = client.post(
        "/governance/notifications",
        json={"channel_type": "carrier_pigeon", "config": {"url": "https://x.example.com"}},
        headers=_HDR,
    )

    assert resp.status_code == 422
    assert "channel_type must be one of" in resp.text
    assert svc.get_channels(_CTX.tenant_id) == []


def test_channel_without_url_is_422() -> None:
    client, svc = _client()

    for channel_type in ("teams", "webhook", "slack"):
        resp = client.post(
            "/governance/notifications",
            json={"channel_type": channel_type, "config": {}},
            headers=_HDR,
        )
        assert resp.status_code == 422, (channel_type, resp.text)
        assert "requires" in resp.text
    assert svc.get_channels(_CTX.tenant_id) == []


def test_non_http_url_is_422() -> None:
    client, _svc = _client()

    resp = client.post(
        "/governance/notifications",
        json={"channel_type": "webhook", "config": {"url": "file:///etc/passwd"}},
        headers=_HDR,
    )

    assert resp.status_code == 422
