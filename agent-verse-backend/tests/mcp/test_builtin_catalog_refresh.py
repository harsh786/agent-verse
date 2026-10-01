"""Lazy built-in provisioning keeps existing tenants current without a startup loop.

The API lifespan used to call ``register_builtin_servers`` for EVERY tenant on
every boot (~76 s for ~390 tenants): it inserted missing credential-free
built-ins and refreshed the platform-owned tool lists. That loop is gone; the
per-tenant lazy path (first listing) now carries a catalogue fingerprint in its
Redis marker so a release that changes the built-in catalogue still refreshes
existing tenants — once, on their next listing — without re-adding a built-in the
tenant removed.
"""

from __future__ import annotations

import builtins
from collections.abc import Iterator
from typing import Any

import pytest

from app.mcp.registry import MCPRegistry
from app.mcp.servers import registry_wiring
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.asyncio


def _tenant(tid: str) -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.FREE, api_key_id=f"k-{tid}")


class _FakeRedis:
    def __init__(self) -> None:
        self._d: dict[str, str] = {}
        self._s: dict[str, builtins.set[str]] = {}
        self.gets = 0

    async def get(self, k: str) -> str | None:
        self.gets += 1
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


def _handler(**_: Any) -> dict[str, Any]:
    return {"ok": True}


def _cfg(server_id: str, tools: list[str]) -> dict[str, Any]:
    return {
        "server_id": server_id,
        "name": server_id,
        "description": "",
        "handler": _handler,
        "tool_definitions": [{"name": t, "description": t} for t in tools],
    }


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    current: list[dict[str, Any]] = [_cfg("builtin-a", ["a1"]), _cfg("builtin-b", ["b1"])]
    monkeypatch.setattr(registry_wiring, "get_builtin_server_configs", lambda: list(current))
    registry_wiring.reset_builtin_catalog_cache()
    yield current
    registry_wiring.reset_builtin_catalog_cache()


def _ship(catalog: list[dict[str, Any]]) -> None:
    """A new release: the (process-static) catalogue fingerprint is recomputed."""
    registry_wiring.reset_builtin_catalog_cache()


def _tools(records: list[tuple[str, Any]]) -> dict[str, list[str]]:
    return {sid: [t["name"] for t in cfg.tool_definitions or []] for sid, cfg in records}


async def test_marker_records_the_catalog_fingerprint(catalog: list[dict[str, Any]]) -> None:
    redis = _FakeRedis()
    registry = MCPRegistry(redis, auto_provision_builtins=True)
    await registry.list_server_records(tenant_ctx=_tenant("t1"))
    marker = redis._d["mcp:builtins_provisioned:t1"]
    assert registry_wiring.builtin_catalog_fingerprint() in marker


async def test_steady_state_listing_does_no_per_builtin_lookups(
    catalog: list[dict[str, Any]],
) -> None:
    redis = _FakeRedis()
    registry = MCPRegistry(redis, auto_provision_builtins=True)
    t = _tenant("t-steady")
    await registry.list_server_records(tenant_ctx=t)
    redis.gets = 0
    await registry.list_server_records(tenant_ctx=t)
    # One marker read + one read per listed server; no catalogue walk.
    assert redis.gets == 1 + 2


async def test_catalog_change_refreshes_tools_and_adds_new_builtin(
    catalog: list[dict[str, Any]],
) -> None:
    redis = _FakeRedis()
    registry = MCPRegistry(redis, auto_provision_builtins=True)
    t = _tenant("t-upgrade")
    await registry.list_server_records(tenant_ctx=t)

    # A release changes builtin-a's tools and ships a brand-new builtin-c.
    catalog[0] = _cfg("builtin-a", ["a1", "a2"])
    catalog.append(_cfg("builtin-c", ["c1"]))
    _ship(catalog)

    tools = _tools(await registry.list_server_records(tenant_ctx=t))
    assert tools["builtin-a"] == ["a1", "a2"]
    assert tools["builtin-c"] == ["c1"]


async def test_catalog_change_does_not_readd_a_removed_builtin(
    catalog: list[dict[str, Any]],
) -> None:
    redis = _FakeRedis()
    registry = MCPRegistry(redis, auto_provision_builtins=True)
    t = _tenant("t-removed")
    await registry.list_server_records(tenant_ctx=t)
    assert await registry.unregister("builtin-b", tenant_ctx=t)

    catalog[0] = _cfg("builtin-a", ["a1", "a2"])
    _ship(catalog)
    tools = _tools(await registry.list_server_records(tenant_ctx=t))
    assert "builtin-b" not in tools
    assert tools["builtin-a"] == ["a1", "a2"]


async def test_legacy_marker_is_upgraded_with_a_refresh(catalog: list[dict[str, Any]]) -> None:
    """Tenants provisioned by the old code (marker "1") get a refresh, no re-adds."""
    redis = _FakeRedis()
    registry = MCPRegistry(redis, auto_provision_builtins=True)
    t = _tenant("t-legacy")
    await registry.list_server_records(tenant_ctx=t)
    assert await registry.unregister("builtin-b", tenant_ctx=t)
    redis._d["mcp:builtins_provisioned:t-legacy"] = "1"
    catalog[0] = _cfg("builtin-a", ["a1", "a3"])
    _ship(catalog)

    tools = _tools(await registry.list_server_records(tenant_ctx=t))
    assert tools == {"builtin-a": ["a1", "a3"]}
    assert (
        registry_wiring.builtin_catalog_fingerprint()
        in redis._d["mcp:builtins_provisioned:t-legacy"]
    )


async def test_register_builtin_handlers_is_process_local_and_io_free(
    catalog: list[dict[str, Any]],
) -> None:
    registry_wiring.register_builtin_handlers()
    assert MCPRegistry.get_builtin_handler("builtin-a") is _handler
    assert MCPRegistry.get_builtin_handler("builtin-b") is _handler
