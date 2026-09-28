"""Regression tests: OpenAPI-imported tools must call the target API authenticated.

``MCPClient._dispatch_openapi_tool`` used to send only ``Content-Type`` — the
connector's configured API key / bearer token was never attached, so every call
to an authenticated API failed with 401. The importer also ignored the spec's
``components.securitySchemes`` (header name / query placement).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from app.mcp.client import MCPClient
from app.mcp.openapi_importer import import_and_register
from app.mcp.registry import MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="t-openapi", plan=PlanTier.PROFESSIONAL, api_key_id="k")
BASE = "https://api.example.com"


def _spec(security_schemes: dict[str, Any], security: list[dict[str, Any]] | None = None) -> str:
    spec: dict[str, Any] = {
        "openapi": "3.0.0",
        "info": {"title": "Items", "version": "1"},
        "paths": {
            "/items/{item_id}": {
                "get": {
                    "summary": "Get item",
                    "parameters": [
                        {"name": "item_id", "in": "path", "required": True},
                        {"name": "verbose", "in": "query"},
                    ],
                }
            },
            "/items": {"post": {"summary": "Create item"}},
        },
        "components": {"securitySchemes": security_schemes},
    }
    if security is not None:
        spec["security"] = security
    return json.dumps(spec)


async def _import(spec: str, auth_config: dict[str, Any]) -> MCPServerConfig:
    captured: list[MCPServerConfig] = []
    registry = MagicMock()

    async def _register(cfg: MCPServerConfig, **_: Any) -> str:
        captured.append(cfg)
        return cfg.server_id

    registry.register = _register
    result = await import_and_register(
        spec_content=spec,
        server_name="Items",
        base_url=BASE,
        registry=registry,
        tenant_ctx=TENANT,
        auth_config=auth_config,
    )
    assert result.get("server_id"), result
    return captured[0]


def _tool(cfg: MCPServerConfig, name: str) -> dict[str, Any]:
    return next(t for t in cfg.tool_definitions if t["name"] == name)


@pytest.fixture
def ssrf_guard():
    with patch("app.mcp.client.assert_public_url") as guard:
        yield guard


@pytest.mark.asyncio
async def test_bearer_token_is_sent(ssrf_guard) -> None:
    cfg = await _import(
        _spec({"bearerAuth": {"type": "http", "scheme": "bearer"}}), {"token": "s3cret-bearer"}
    )
    client = MCPClient(registry=AsyncMock())
    with respx.mock:
        route = respx.get(f"{BASE}/items/42").mock(
            return_value=httpx.Response(200, json={"id": 42})
        )
        result = await client._dispatch_openapi_tool(
            cfg, _tool(cfg, "get_items_item_id"), {"item_id": 42, "verbose": "1"}, TENANT
        )
    assert result.success, result.error
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer s3cret-bearer"
    # Path parameters are substituted, the rest stay in the query string.
    assert req.url.path == "/items/42"
    assert req.url.params["verbose"] == "1"
    assert "item_id" not in req.url.params
    ssrf_guard.assert_called()
    assert ssrf_guard.call_args.args[0].startswith(f"{BASE}/items/42")


@pytest.mark.asyncio
async def test_api_key_uses_the_spec_header_name(ssrf_guard) -> None:
    cfg = await _import(
        _spec({"k": {"type": "apiKey", "in": "header", "name": "X-Items-Key"}}),
        {"api_key": "key-123"},
    )
    client = MCPClient(registry=AsyncMock())
    with respx.mock:
        route = respx.post(f"{BASE}/items").mock(return_value=httpx.Response(201, json={}))
        result = await client._dispatch_openapi_tool(
            cfg, _tool(cfg, "post_items"), {"name": "x"}, TENANT
        )
    assert result.success, result.error
    assert route.calls.last.request.headers["x-items-key"] == "key-123"


@pytest.mark.asyncio
async def test_api_key_in_query_is_sent_and_never_leaks_into_errors(ssrf_guard) -> None:
    cfg = await _import(
        _spec({"q": {"type": "apiKey", "in": "query", "name": "apikey"}}),
        {"api_key": "query-secret-999"},
    )
    client = MCPClient(registry=AsyncMock())
    with respx.mock:
        route = respx.post(f"{BASE}/items").mock(
            return_value=httpx.Response(401, json={"message": "bad key"})
        )
        result = await client._dispatch_openapi_tool(cfg, _tool(cfg, "post_items"), {}, TENANT)
    assert route.calls.last.request.url.params["apikey"] == "query-secret-999"
    assert "apikey" not in route.calls.last.request.headers
    assert result.success is False
    assert "401" in (result.error or "")
    assert "query-secret-999" not in (result.error or "")


@pytest.mark.asyncio
async def test_connector_auth_applies_without_security_schemes(ssrf_guard) -> None:
    """A hand-built connector (no spec) still gets its configured auth."""
    cfg = MCPServerConfig(
        name="raw",
        base_url=BASE,
        auth_type="api_key",
        auth_config={"api_key": "raw-key", "header_name": "X-Raw"},
    )
    client = MCPClient(registry=AsyncMock())
    with respx.mock:
        route = respx.post(f"{BASE}/items").mock(return_value=httpx.Response(200, json={}))
        await client._dispatch_openapi_tool(
            cfg, {"name": "post_items", "http_method": "POST", "http_path": "/items"}, {}, TENANT
        )
    assert route.calls.last.request.headers["x-raw"] == "raw-key"


@pytest.mark.asyncio
async def test_ssrf_guard_blocks_private_target_before_any_request() -> None:
    cfg = MCPServerConfig(
        name="meta",
        base_url="http://169.254.169.254",
        auth_type="bearer",
        auth_config={"token": "t"},
    )
    client = MCPClient(registry=AsyncMock())
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route().mock(return_value=httpx.Response(200, json={}))
        result = await client._dispatch_openapi_tool(
            cfg, {"name": "x", "http_method": "GET", "http_path": "/latest"}, {}, TENANT
        )
    assert result.success is False
    assert "ssrf" in (result.error or "").lower()
    assert not route.called
