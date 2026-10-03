"""OAUTH-05: OAuth code-exchange failures get honest status codes.

They all answered 200 {status: "error"} — invalid state, a provider rejecting
the code, an unreachable token endpoint alike — with raw exception text, and an
unexpected error was reported as "invalid state".
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.mcp.oauth import OAuthFlowManager
from app.mcp.registry import MCPServerConfig
from tests.api.test_connectors_comprehensive2 import _CTX, _VALID_KEY, _make_app, _make_registry

_TOKEN_URL = "https://93.184.216.34/oauth/token"


def _setup() -> tuple[TestClient, OAuthFlowManager, str]:
    reg = _make_registry()
    sid = asyncio.run(
        reg.register(
            MCPServerConfig(
                name="oauth thing",
                url="https://api.example.com",
                auth_type="oauth_ac",
                auth_config={"token_url": _TOKEN_URL, "client_id": "c"},
            ),
            tenant_ctx=_CTX,
        )
    )
    app = _make_app(reg)
    mgr = OAuthFlowManager()
    app.state.oauth_manager = mgr
    return TestClient(app, raise_server_exceptions=False), mgr, sid


def _callback(client: TestClient, sid: str, state: str) -> httpx.Response:
    return client.get(
        "/connectors/oauth/callback",
        params={"code": "c", "state": state, "server_id": sid},
        headers={"X-API-Key": _VALID_KEY},
    )


def test_unknown_state_is_400() -> None:
    client, _mgr, sid = _setup()
    resp = _callback(client, sid, "nope")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "oauth_invalid_state"


@pytest.mark.parametrize(
    ("mock", "status", "code"),
    [
        (httpx.Response(400, json={"error": "invalid_grant", "secret": "s3"}), 502,
         "oauth_provider_rejected"),
        (httpx.ConnectError("refused"), 504, "oauth_provider_unreachable"),
        (httpx.Response(200, json={"token_type": "Bearer"}), 502, "oauth_provider_bad_response"),
    ],
)
def test_provider_failures_have_distinct_codes(mock: object, status: int, code: str) -> None:
    client, mgr, sid = _setup()
    state = mgr.start_flow(server_id=sid, tenant_ctx=_CTX)["state"]
    with respx.mock:
        route = respx.post(_TOKEN_URL)
        if isinstance(mock, Exception):
            route.mock(side_effect=mock)
        else:
            route.mock(return_value=mock)
        resp = _callback(client, sid, state)
    assert resp.status_code == status, resp.text
    assert resp.json()["detail"]["code"] == code
    assert "s3" not in resp.text and "refused" not in resp.text


def test_unexpected_error_is_not_reported_as_invalid_state() -> None:
    client, mgr, sid = _setup()
    state = mgr.start_flow(server_id=sid, tenant_ctx=_CTX)["state"]
    with respx.mock:
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, content=b"not json"))
        resp = _callback(client, sid, state)
    assert resp.status_code == 502
    assert "state" not in resp.json()["detail"]["message"].lower()
