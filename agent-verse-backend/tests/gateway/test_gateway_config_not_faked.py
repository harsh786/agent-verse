"""Regression: gateway config endpoints must not fake success.

Old bug: ``GET /v1/gateway/{org_id}/config`` returned hard-coded defaults,
``PUT`` echoed the body back without storing anything, and
``GET /v1/gateway/{org_id}/channels/status`` returned canned "active" statuses
(and a made-up ``wss://mcp.agentverse.io`` endpoint) for every org. All three sit
under the ``/v1/gateway/`` TenantMiddleware bypass, so they also answered any
unauthenticated caller for any org id. A client saving config got a 200 and the
change silently vanished. They now answer 501 Not Implemented.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.router import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_put_config_does_not_claim_to_save() -> None:
    r = _client().put("/v1/gateway/org1/config", json={"telegram_enabled": True})
    assert r.status_code == 501


def test_get_config_does_not_return_canned_defaults() -> None:
    assert _client().get("/v1/gateway/org1/config").status_code == 501


def test_channel_status_does_not_return_canned_statuses() -> None:
    assert _client().get("/v1/gateway/org1/channels/status").status_code == 501
