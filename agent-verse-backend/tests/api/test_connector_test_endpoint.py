"""POST /connectors/{id}/test must not report success it did not observe."""

from __future__ import annotations

import asyncio

import httpx
import respx
from fastapi.testclient import TestClient

from app.mcp.registry import MCPServerConfig
from tests.api.test_connectors_comprehensive2 import _CTX, _VALID_KEY, _make_app, _make_registry


def _register(cfg: MCPServerConfig):  # type: ignore[no-untyped-def]
    reg = _make_registry()
    sid = asyncio.run(reg.register(cfg, tenant_ctx=_CTX))
    return reg, sid


def test_unresolvable_stored_secret_fails_without_contacting_vendor() -> None:
    reg, sid = _register(
        MCPServerConfig(
            name="jira",
            url="https://93.184.216.34",
            auth_type="basic",
            auth_config={"email": "a@b.com", "api_token": "vault://connectors/missing/api_token"},
        )
    )
    client = TestClient(_make_app(reg))
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route().mock(return_value=httpx.Response(200, json={}))
        resp = client.post(f"/connectors/{sid}/test", headers={"X-API-Key": _VALID_KEY})
    body = resp.json()
    assert body["status"] == "failed"
    assert "could not be resolved" in body["error"]
    assert not route.called  # the raw vault:// ref was never sent as a credential


def test_direct_probe_of_internal_legacy_url_is_refused() -> None:
    reg, sid = _register(
        MCPServerConfig(name="jira", url="http://169.254.169.254", auth_type="none")
    )
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route().mock(return_value=httpx.Response(200, json={}))
        resp = client.post(f"/connectors/{sid}/test", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 400
    assert not route.called


def test_generic_probe_rejected_credentials_is_failed() -> None:
    reg, sid = _register(
        MCPServerConfig(
            name="custom thing",
            url="https://93.184.216.34/api",
            auth_type="bearer",
            auth_config={"token": "nope"},
        )
    )
    client = TestClient(_make_app(reg))
    with respx.mock:
        respx.get("https://93.184.216.34/api").mock(return_value=httpx.Response(403))
        resp = client.post(f"/connectors/{sid}/test", headers={"X-API-Key": _VALID_KEY})
    body = resp.json()
    assert body["status"] == "failed"
    assert body["http_status"] == 403
