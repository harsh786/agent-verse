"""OAUTH-PASSTHROUGH: OAuth built-ins (Google, Microsoft) receive the tenant
connector's OWN access token from the MCP OAuth/PKCE token store.

``_dispatch_builtin_tool`` passed only ``auth_config`` as credentials, so a
connector authorised through the OAuth flow reached its handler with no token:
it used to fall back to the platform's ``GOOGLE_ACCESS_TOKEN`` and, since
BUILTIN-CREDS, failed with "configure credentials". The token is now read from
the store per connection (refreshed when expired), never from the platform env.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.mcp.client import MCPClient
from app.mcp.oauth import OAuthToken
from app.mcp.registry import MCPServerConfig
from app.mcp.servers.registry_wiring import get_builtin_server_configs
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="t-oauth", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _FakeOAuth:
    def __init__(self, tokens: dict[tuple[str, str], OAuthToken]) -> None:
        self.tokens = tokens
        self.refreshed: list[str] = []

    async def aget_token(self, tenant_id: str, server_id: str) -> OAuthToken | None:
        return self.tokens.get((tenant_id, server_id))

    def get_token(self, tenant_id: str, server_id: str) -> OAuthToken | None:
        return self.tokens.get((tenant_id, server_id))

    async def refresh_token(self, *, tenant_id: str, server_id: str, **kw: Any) -> OAuthToken:
        self.refreshed.append(server_id)
        fresh = OAuthToken(access_token=f"refreshed-{server_id}")
        self.tokens[(tenant_id, server_id)] = fresh
        return fresh


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    async def _send(self: Any, request: httpx.Request, **kw: Any) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"files": []}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", _send)
    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "PLATFORM-GOOGLE-TOKEN")
    monkeypatch.setenv("MICROSOFT_ACCESS_TOKEN", "PLATFORM-MS-TOKEN")
    return requests


def _cfg(builtin_id: str, server_id: str, name: str) -> MCPServerConfig:
    (spec,) = [c for c in get_builtin_server_configs() if c["server_id"] == builtin_id]
    return MCPServerConfig(
        server_id=server_id,
        name=name,
        base_url="builtin://",
        auth_type="oauth_ac",
        auth_config={"client_id": "cid", "token_url": "https://oauth2.googleapis.com/token"},
        builtin_type=builtin_id,
        builtin_handler=spec["handler"],
    )


def _client(oauth: Any) -> MCPClient:
    client = MCPClient(registry=AsyncMock())
    client._oauth_manager = oauth
    return client


def _auth_headers(requests: list[httpx.Request]) -> list[str]:
    return [r.headers.get("authorization", "") for r in requests]


async def test_google_builtin_uses_the_connections_oauth_token(
    sent: list[httpx.Request],
) -> None:
    cfg = _cfg("builtin-google-drive", "builtin-google-drive:team-drive", "team-drive")
    oauth = _FakeOAuth({("t-oauth", cfg.server_id): OAuthToken(access_token="tenant-drive-at")})

    result = await _client(oauth)._call_tool_impl(
        cfg, cfg.server_id, "drive_list_files", {}, TENANT
    )

    assert result.success, result.error
    assert _auth_headers(sent) == ["Bearer tenant-drive-at"]


async def test_two_connections_each_send_their_own_token(sent: list[httpx.Request]) -> None:
    a = _cfg("builtin-google-drive", "builtin-google-drive:a", "drive-a")
    b = _cfg("builtin-google-drive", "builtin-google-drive:b", "drive-b")
    oauth = _FakeOAuth(
        {
            ("t-oauth", a.server_id): OAuthToken(access_token="at-a"),
            ("t-oauth", b.server_id): OAuthToken(access_token="at-b"),
        }
    )
    client = _client(oauth)

    await client._call_tool_impl(a, a.server_id, "drive_list_files", {}, TENANT)
    await client._call_tool_impl(b, b.server_id, "drive_list_files", {}, TENANT)

    assert _auth_headers(sent) == ["Bearer at-a", "Bearer at-b"]


async def test_expired_token_is_refreshed_before_use(sent: list[httpx.Request]) -> None:
    cfg = _cfg("builtin-google-drive", "builtin-google-drive:x", "drive-x")
    expired = OAuthToken(access_token="old", refresh_token="rt", expires_in=0, obtained_at=0)
    oauth = _FakeOAuth({("t-oauth", cfg.server_id): expired})

    result = await _client(oauth)._call_tool_impl(
        cfg, cfg.server_id, "drive_list_files", {}, TENANT
    )

    assert result.success, result.error
    assert oauth.refreshed == [cfg.server_id]
    assert _auth_headers(sent) == [f"Bearer refreshed-{cfg.server_id}"]


async def test_microsoft_builtin_uses_the_connections_oauth_token(
    sent: list[httpx.Request],
) -> None:
    outlook = [
        c for c in get_builtin_server_configs() if c["server_id"] == "builtin-microsoft-outlook"
    ]
    assert outlook, "microsoft outlook built-in expected"
    cfg = _cfg("builtin-microsoft-outlook", "builtin-microsoft-outlook:mail", "mail")
    oauth = _FakeOAuth({("t-oauth", cfg.server_id): OAuthToken(access_token="tenant-ms-at")})
    tool = outlook[0]["tool_definitions"][0]
    args = dict.fromkeys(tool.get("parameters", {}).get("required", []), "x")

    await _client(oauth)._call_tool_impl(cfg, cfg.server_id, tool["name"], args, TENANT)

    assert sent and all(h == "Bearer tenant-ms-at" for h in _auth_headers(sent))


@pytest.mark.parametrize("oauth", [None, _FakeOAuth({})], ids=["no-manager", "no-token"])
async def test_no_token_fails_closed_without_platform_fallback(
    sent: list[httpx.Request], oauth: Any
) -> None:
    cfg = _cfg("builtin-google-drive", "builtin-google-drive:y", "drive-y")

    result = await _client(oauth)._call_tool_impl(
        cfg, cfg.server_id, "drive_list_files", {}, TENANT
    )

    assert result.success is False
    assert "reconnect" in (result.error or "").lower()
    assert sent == []
