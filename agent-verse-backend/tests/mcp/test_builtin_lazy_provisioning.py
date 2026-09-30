"""Credential-free built-ins must reach tenants created after startup.

The lifespan only wired built-in connectors (web_search, http_request, OCR...) for
tenants that already existed at boot. A tenant that signed up — or was JIT
provisioned by SSO — afterwards had none until the next restart, so a workflow
``tool`` step calling ``web_search`` failed with "no registered connector exposes
tool 'web_search'" (user report: the digest workflow never finished).
"""

from __future__ import annotations

import builtins
from typing import Any

import pytest

from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry
from app.mcp.servers import utility_server
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.asyncio


def _tenant(tid: str) -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.FREE, api_key_id=f"k-{tid}")


class _FakeRedis:
    def __init__(self) -> None:
        self._d: dict[str, str] = {}
        self._s: dict[str, builtins.set[str]] = {}

    async def get(self, k: str) -> str | None:
        return self._d.get(k)

    async def set(self, k: str, v: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and k in self._d:
            return False
        self._d[k] = v
        return True

    async def delete(self, k: str) -> int:
        return 1 if self._d.pop(k, None) is not None else 0

    async def sadd(self, k: str, v: str) -> None:
        self._s.setdefault(k, set()).add(v)

    async def srem(self, k: str, v: str) -> None:
        self._s.get(k, set()).discard(v)

    async def smembers(self, k: str) -> builtins.set[str]:
        return set(self._s.get(k, set()))


def _tool_names(records: list[tuple[str, Any]]) -> set[str]:
    return {t.get("name") for _, cfg in records for t in (cfg.tool_definitions or [])}


async def test_new_tenant_gets_credential_free_builtins_on_first_listing() -> None:
    registry = MCPRegistry(_FakeRedis(), auto_provision_builtins=True)
    records = await registry.list_server_records(tenant_ctx=_tenant("signed-up-later"))
    assert "web_search" in _tool_names(records)
    assert "http_request" in _tool_names(records)


async def test_provisioning_runs_once_so_a_removed_builtin_stays_removed() -> None:
    registry = MCPRegistry(_FakeRedis(), auto_provision_builtins=True)
    t = _tenant("t-remove")
    records = await registry.list_server_records(tenant_ctx=t)
    utility_id = next(sid for sid, cfg in records if any(
        d.get("name") == "web_search" for d in cfg.tool_definitions or []
    ))
    assert await registry.unregister(utility_id, tenant_ctx=t)
    again = await registry.list_server_records(tenant_ctx=t)
    assert "web_search" not in _tool_names(again)


async def test_plain_registry_does_not_provision() -> None:
    registry = MCPRegistry(_FakeRedis())
    assert await registry.list_server_records(tenant_ctx=_tenant("plain")) == []


async def test_workflow_tool_step_resolves_web_search_for_new_tenant() -> None:
    class _StubSearch:
        async def run(self, **kwargs: Any) -> dict[str, Any]:
            return {"results": [{"title": f"hit for {kwargs.get('query')}"}]}

        async def execute(self, **kwargs: Any) -> dict[str, Any]:
            return await self.run(**kwargs)

    utility_server.set_tools({"web_search": _StubSearch()})
    try:
        registry = MCPRegistry(_FakeRedis(), auto_provision_builtins=True)
        client = MCPClient(registry)
        result = await client.call_tool_by_name(
            tool_name="web_search",
            arguments={"query": "Tesla latest news"},
            tenant_ctx=_tenant("brand-new"),
        )
    finally:
        utility_server.set_tools(None)
    assert "no registered connector" not in (result.error or "")
