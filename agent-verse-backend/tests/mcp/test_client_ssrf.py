"""SSRF guard tests for MCPClient.discover_tools (P0-2).

Ensures that discover_tools raises before making any outbound HTTP request when
the registered server URL targets a loopback, private-range, or metadata address.
"""
from __future__ import annotations

import builtins
from unittest.mock import patch

import pytest

from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.net.ssrf_guard import SSRFError
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(
    tenant_id="ssrf-test",
    plan=PlanTier.STARTER,
    api_key_id="ssrf-key-1",
)

_BLOCKED_URLS = [
    "http://127.0.0.1:9001",           # loopback
    "http://localhost:9001",            # loopback hostname
    "http://192.168.1.100/mcp",        # RFC-1918 private
    "http://10.0.0.1/tools",           # RFC-1918 private
    "http://169.254.169.254/latest",   # AWS metadata endpoint
]


class _FakeRedis:
    """Minimal in-memory Redis stub for the registry."""

    def __init__(self) -> None:
        self._d: dict[str, str] = {}
        self._s: dict[str, builtins.set[str]] = {}

    async def get(self, k: str) -> str | None:
        return self._d.get(k)

    async def set(self, k: str, v: str, ex: int | None = None) -> None:
        self._d[k] = v

    async def delete(self, k: str) -> int:
        self._d.pop(k, None)
        return 1

    async def sadd(self, k: str, v: str) -> None:
        self._s.setdefault(k, set()).add(v)

    async def srem(self, k: str, v: str) -> None:
        self._s.get(k, set()).discard(v)

    async def smembers(self, k: str) -> builtins.set[str]:
        return self._s.get(k, set())


async def _make_client_with_url(url: str) -> tuple[MCPClient, str]:
    """Register a server at *url* and return (MCPClient, server_id)."""
    redis = _FakeRedis()
    registry = MCPRegistry(redis=redis)
    cfg = MCPServerConfig(name="evil-server", url=url, auth_type="none")
    server_id = await registry.register(cfg, tenant_ctx=TENANT)
    client = MCPClient(registry=registry)
    return client, server_id


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_url", _BLOCKED_URLS)
async def test_discover_tools_blocks_private_urls(bad_url: str) -> None:
    """discover_tools must raise ValueError (wrapping SSRFError) for private/loopback URLs."""
    client, server_id = await _make_client_with_url(bad_url)

    with pytest.raises((ValueError, SSRFError)):
        await client.discover_tools(server_id=server_id, tenant_ctx=TENANT)


@pytest.mark.asyncio
async def test_discover_tools_blocks_loopback_no_http_call() -> None:
    """No outbound HTTP must be attempted when the SSRF guard fires."""
    client, server_id = await _make_client_with_url("http://127.0.0.1:9001")

    with patch("httpx.AsyncClient") as mock_http, pytest.raises((ValueError, SSRFError)):
        await client.discover_tools(server_id=server_id, tenant_ctx=TENANT)

    mock_http.assert_not_called()


@pytest.mark.asyncio
async def test_discover_tools_unknown_server_returns_empty() -> None:
    """discover_tools with an unregistered server_id returns []."""
    redis = _FakeRedis()
    registry = MCPRegistry(redis=redis)
    client = MCPClient(registry=registry)

    result = await client.discover_tools(server_id="nonexistent", tenant_ctx=TENANT)

    assert result == []
