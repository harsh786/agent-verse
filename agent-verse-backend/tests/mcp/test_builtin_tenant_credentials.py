"""Regression tests: built-in MCP servers vs tenant-configured connectors.

Two bugs in ``register_builtin_servers`` (run for every tenant at startup):

1. When the platform env var was set (e.g. GITHUB_TOKEN) it unconditionally
   ``register()``-ed a fresh built-in config under the canonical server_id
   ("builtin-github") — the same id a tenant's own GitHub connector adopts — so
   the tenant's config (and its credentials) was silently replaced on restart.
2. That fresh config carried no credentials, so the handler fell back to the
   platform's GITHUB_TOKEN: every tenant's agent acted with the platform's
   GitHub identity (confused deputy).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.mcp.client import MCPClient
from app.mcp.registry import MCPServerConfig
from app.mcp.servers import github_server
from app.mcp.servers.registry_wiring import register_builtin_servers
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="tenant-a", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _DictRegistry:
    """Minimal per-tenant registry fake with the MCPRegistry surface used here."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], MCPServerConfig] = {}

    async def register(self, cfg: MCPServerConfig, *, tenant_ctx: Any) -> str:
        self.rows[(tenant_ctx.tenant_id, cfg.server_id)] = cfg
        return cfg.server_id

    async def get(self, server_id: str, *, tenant_ctx: Any) -> MCPServerConfig | None:
        return self.rows.get((tenant_ctx.tenant_id, server_id))

    async def unregister(self, server_id: str, *, tenant_ctx: Any) -> bool:
        return self.rows.pop((tenant_ctx.tenant_id, server_id), None) is not None


@pytest.mark.asyncio
async def test_startup_does_not_overwrite_tenant_github_connector(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_platform_token")
    reg = _DictRegistry()
    tenant_cfg = MCPServerConfig(
        server_id="builtin-github",
        name="GitHub",
        url="https://api.github.com",
        auth_type="bearer",
        auth_config={"token": "vault://connectors/builtin-github/token"},
        auto_approve=True,
    )
    await reg.register(tenant_cfg, tenant_ctx=TENANT)

    await register_builtin_servers(reg, TENANT)

    after = await reg.get("builtin-github", tenant_ctx=TENANT)
    assert after is not None
    assert after.auth_config == {"token": "vault://connectors/builtin-github/token"}
    assert after.url == "https://api.github.com"
    assert after.auto_approve is True


@pytest.mark.asyncio
async def test_platform_github_token_is_not_wired_into_tenant_connectors(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_platform_token")
    reg = _DictRegistry()

    await register_builtin_servers(reg, TENANT)

    assert await reg.get("builtin-github", tenant_ctx=TENANT) is None


@pytest.mark.asyncio
async def test_stale_platform_wired_builtin_is_removed(monkeypatch) -> None:
    """A credential-less builtin-github left behind by the old wiring is cleaned up."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_platform_token")
    reg = _DictRegistry()
    await reg.register(
        MCPServerConfig(server_id="builtin-github", name="GitHub", base_url="builtin://"),
        tenant_ctx=TENANT,
    )

    await register_builtin_servers(reg, TENANT)

    assert await reg.get("builtin-github", tenant_ctx=TENANT) is None


@pytest.mark.asyncio
async def test_credential_free_builtin_is_still_inserted_once() -> None:
    from app.mcp.servers import utility_server

    reg = _DictRegistry()
    await register_builtin_servers(reg, TENANT)
    first = await reg.get(utility_server.SERVER_ID, tenant_ctx=TENANT)
    assert first is not None and first.tool_definitions

    # A tenant's own tweak (e.g. disabling it) survives the next restart.
    first.enabled = False
    await register_builtin_servers(reg, TENANT)
    again = await reg.get(utility_server.SERVER_ID, tenant_ctx=TENANT)
    assert again is not None and again.enabled is False


@pytest.mark.asyncio
async def test_github_handler_never_falls_back_to_platform_token(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_platform_token")
    with respx.mock:
        route = respx.get("https://api.github.com/users/o/repos").mock(
            return_value=httpx.Response(200, json=[])
        )
        await github_server.call_tool("github_list_repos", {"owner": "o"})
        assert "authorization" not in route.calls.last.request.headers

        await github_server.call_tool(
            "github_list_repos", {"owner": "o"}, credentials={"token": "tenant-tok"}
        )
        assert route.calls.last.request.headers["authorization"] == "Bearer tenant-tok"


@pytest.mark.asyncio
async def test_builtin_dispatch_refuses_when_tenant_has_no_credentials(monkeypatch) -> None:
    """Defence in depth for the ~300 handlers that still read os.getenv(): a
    credentialed built-in with no tenant credentials is refused before the
    handler (and its env fallback) ever runs."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_platform_token")
    seen: list[dict[str, Any]] = []

    async def handler(
        tool_name: str, arguments: dict[str, Any], *, credentials: dict[str, Any]
    ) -> dict[str, Any]:
        seen.append(credentials)
        return {"ok": True}

    cfg = MCPServerConfig(
        server_id="builtin-github", name="GitHub", base_url="builtin://", builtin_handler=handler
    )
    client = MCPClient(registry=AsyncMock())

    result = await client._dispatch_builtin_tool(cfg, "github_list_repos", {}, TENANT)

    assert result.success is False
    assert "credentials" in (result.error or "").lower()
    assert seen == []

    cfg.auth_config = {"token": "tenant-tok"}
    result = await client._dispatch_builtin_tool(cfg, "github_list_repos", {}, TENANT)
    assert result.success is True
    assert seen[-1]["token"] == "tenant-tok"
