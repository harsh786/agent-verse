"""OAUTH-04: an OAuth connector without a usable token never calls out unauthenticated.

``MCPClient._oauth_access_token`` swallowed refresh errors (``contextlib.suppress``)
and lookup errors, and ``_build_auth_headers`` then built NO Authorization header,
so the tool request silently went to the provider unauthenticated. Now the
refresh failure is logged and the HTTP dispatch refuses with
:class:`OAuthReauthorizationRequiredError` (the connection must be authorized
again) before any request is sent.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.mcp.client import MCPClient
from app.mcp.oauth import OAuthReauthorizationRequiredError, OAuthToken
from app.mcp.registry import MCPServerConfig


class _Ctx:
    tenant_id = "t1"


class _Manager:
    def __init__(self, token: OAuthToken | None, refresh_exc: Exception | None = None) -> None:
        self.token = token
        self.refresh_exc = refresh_exc
        self.refresh_calls = 0

    async def aget_token(self, tenant_id: str, server_id: str) -> OAuthToken | None:
        return self.token

    async def refresh_token(self, **_: Any) -> OAuthToken | None:
        self.refresh_calls += 1
        if self.refresh_exc is not None:
            raise self.refresh_exc
        return None


def _client(manager: _Manager) -> MCPClient:
    client = MCPClient.__new__(MCPClient)
    client._oauth_manager = manager
    return client


def _cfg() -> MCPServerConfig:
    return MCPServerConfig(
        name="gdrive", url="https://mcp.example.com/mcp", auth_type="oauth_ac",
        server_id="srv",
    )


class _RecordingLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def _record(self, event: str, *args: Any, **kwargs: Any) -> None:
        self.lines.append(f"{event} {args} {kwargs}")

    warning = error = info = debug = _record


async def test_refresh_error_is_logged_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _Manager(OAuthToken(access_token="old", refresh_token="r", expires_in=0),
                   refresh_exc=RuntimeError("invalid_grant"))
    log = _RecordingLogger()
    monkeypatch.setattr("app.mcp.client.logger", log)
    token = await _client(mgr)._oauth_access_token(_cfg(), tenant_ctx=_Ctx(), server_id="srv")  # type: ignore[arg-type]
    assert token is None
    assert mgr.refresh_calls == 1
    assert any("oauth_token_refresh_failed" in x and "invalid_grant" in x for x in log.lines)


@pytest.mark.parametrize(
    "manager",
    [
        _Manager(None),
        _Manager(OAuthToken(access_token="old", refresh_token="r", expires_in=0),
                 refresh_exc=RuntimeError("invalid_grant")),
        _Manager(OAuthToken(access_token="old", refresh_token="", expires_in=0)),
    ],
    ids=["no-token", "refresh-failed", "expired-no-refresh"],
)
async def test_http_auth_headers_refuse_without_a_token(manager: _Manager) -> None:
    with pytest.raises(OAuthReauthorizationRequiredError) as info:
        await _client(manager)._build_auth_headers(_cfg(), tenant_ctx=_Ctx(), server_id="srv")  # type: ignore[arg-type]
    assert info.value.server_id == "srv"
    assert "authoriz" in str(info.value).lower()


async def test_no_oauth_manager_refuses_too() -> None:
    client = MCPClient.__new__(MCPClient)
    client._oauth_manager = None
    with pytest.raises(OAuthReauthorizationRequiredError):
        await client._build_auth_headers(_cfg(), tenant_ctx=_Ctx(), server_id="srv")  # type: ignore[arg-type]


async def test_valid_token_still_sent() -> None:
    mgr = _Manager(OAuthToken(access_token="fresh", expires_in=3600))
    headers = await _client(mgr)._build_auth_headers(_cfg(), tenant_ctx=_Ctx(), server_id="srv")  # type: ignore[arg-type]
    assert headers == {"Authorization": "Bearer fresh"}


async def test_call_tool_never_sends_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end through call_tool: the HTTP client is never opened."""
    from app.mcp.registry import MCPRegistry
    from app.tenancy.context import PlanTier, TenantContext

    import fakeredis.aioredis

    registry = MCPRegistry(fakeredis.aioredis.FakeRedis())
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    server_id = await registry.register(_cfg(), tenant_ctx=ctx)
    client = MCPClient(registry)
    client._oauth_manager = _Manager(None)
    monkeypatch.setattr("app.mcp.client._assert_egress_allowed", lambda *a, **k: None)

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("an unauthenticated request was attempted")

    monkeypatch.setattr(client, "_http_client", _boom)
    result = await client.call_tool(
        server_id=server_id, tool_name="search", arguments={}, tenant_ctx=ctx
    )
    assert result.success is False
    assert "authoriz" in result.error.lower()
