"""Built-in dispatch resolves connector secrets per tenant and fails closed.

Regression: ``_dispatch_builtin_tool`` looked the ref up in the process-global
secret mapping *before* the tenant-aware resolver. Canonical ids such as
``builtin-jira`` are shared by every tenant, so tenant B's connector could be
handed tenant A's secret. Resolver errors were swallowed and the raw
``vault://`` reference was passed to the handler as the credential.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.providers.vault import store_connector_secret
from app.tenancy.context import PlanTier, TenantContext

_REF = "vault://connectors/builtin-shared/api_token"


def _ctx(tid: str) -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _cfg(handler: Any) -> MCPServerConfig:
    return MCPServerConfig(
        server_id="builtin-shared",
        name="Shared Builtin",
        url="builtin://shared",
        builtin_handler=handler,
        auth_config={"api_token": _REF},
    )


@pytest.mark.asyncio
async def test_global_mapping_is_not_consulted_before_tenant_resolver() -> None:
    store_connector_secret(_REF, "TENANT-A-SECRET")  # process-global legacy mapping
    seen: dict[str, Any] = {}

    async def handler(tool_name: str, args: dict, credentials: dict) -> dict:
        seen.update(credentials)
        return {"ok": True}

    tenant_store = {("tenant-a", _REF): "TENANT-A-SECRET"}

    async def resolver(ref: str, tenant_ctx: Any) -> str | None:
        return tenant_store.get((tenant_ctx.tenant_id, ref))

    client = MCPClient(registry=MCPRegistry(redis=None), secret_resolver=resolver)
    result = await client._dispatch_builtin_tool(_cfg(handler), "t", {}, _ctx("tenant-b"))
    assert result.success is False
    assert "TENANT-A-SECRET" not in str(seen)

    ok = await client._dispatch_builtin_tool(_cfg(handler), "t", {}, _ctx("tenant-a"))
    assert ok.success is True
    assert seen["api_token"] == "TENANT-A-SECRET"


@pytest.mark.asyncio
async def test_unresolvable_ref_never_reaches_handler() -> None:
    called = False

    async def handler(tool_name: str, args: dict, credentials: dict) -> dict:
        nonlocal called
        called = True
        return {"ok": True}

    async def resolver(ref: str, tenant_ctx: Any) -> str | None:
        return None

    client = MCPClient(registry=MCPRegistry(redis=None), secret_resolver=resolver)
    result = await client._dispatch_builtin_tool(_cfg(handler), "t", {}, _ctx("tenant-x"))
    assert result.success is False
    assert called is False
