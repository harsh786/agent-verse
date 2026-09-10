"""Full-stack e2e: OCR (``extract_document``) is agent-callable by name.

Boots the real app (``create_app(manage_pools=True)`` — Postgres + Redis), then
proves the builtin *utility* MCP server is on a tenant's builtin surface and that
a ``call_tool("extract_document", ...)`` reaches the REAL pipeline
(``OcrDocumentTool`` → the one ``OcrEngine``) and returns an extracted-document
result. Text may be empty (no Tesseract binary / no vision provider in CI) but
the honest metadata (``source_format`` / ``degraded``) is always present.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from app.mcp.servers.registry_wiring import register_builtin_servers
from app.mcp.servers.utility_server import SERVER_ID as UTILITY_SERVER_ID
from app.tenancy.context import PlanTier, TenantContext

pytestmark = [
    pytest.mark.e2e_full,
    pytest.mark.asyncio(loop_scope="session"),
]


def _tiny_png_b64() -> str:
    """A minimal valid 1x1 PNG (openable by PIL) as base64."""
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    return base64.b64encode(png).decode()


async def _seed_tenant_ctx(client: Any) -> TenantContext:
    import uuid

    resp = await client.post(
        "/tenants/signup",
        json={"name": "OCR E2E", "email": f"ocr-e2e-{uuid.uuid4().hex[:12]}@example.com"},
    )
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    body = resp.json()
    return TenantContext(
        tenant_id=body["tenant_id"],
        plan=PlanTier(body.get("plan", "free")),
        api_key_id=body.get("api_key_id", "ocr-e2e"),
        roles=("admin",),
    )


async def test_builtin_utility_server_registered_for_tenant(
    app: Any, client: Any
) -> None:
    """The builtin-utility server registers unconditionally (no requires_env)."""
    tenant_ctx = await _seed_tenant_ctx(client)
    registry = app.state.mcp_registry

    await register_builtin_servers(registry, tenant_ctx)

    cfg = await registry.get(UTILITY_SERVER_ID, tenant_ctx=tenant_ctx)
    assert cfg is not None, "builtin-utility server was not registered"
    tool_names = {t.get("name") for t in cfg.tool_definitions}
    assert "extract_document" in tool_names

    # And it surfaces in the agent's tool list (tools/list) for this tenant.
    mcp_client = app.state.mcp_client
    tools = await mcp_client.discover_all_tools(tenant_ctx=tenant_ctx)
    by_name = {t.name: t for t in tools}
    assert "extract_document" in by_name
    assert by_name["extract_document"].server_id == UTILITY_SERVER_ID


async def test_extract_document_call_returns_ocr_via_real_pipeline(
    app: Any, client: Any
) -> None:
    """call_tool('extract_document', ...) routes through the real OcrEngine."""
    tenant_ctx = await _seed_tenant_ctx(client)
    registry = app.state.mcp_registry
    mcp_client = app.state.mcp_client

    await register_builtin_servers(registry, tenant_ctx)

    result = await mcp_client.call_tool(
        server_id=UTILITY_SERVER_ID,
        tool_name="extract_document",
        arguments={"document_base64": _tiny_png_b64(), "content_type": "image/png"},
        tenant_ctx=tenant_ctx,
    )

    assert result.success is True, f"call failed: {result.error}"
    output = result.output
    assert isinstance(output, dict)
    # Extracted text may be empty (no OCR binary/provider) but the honest
    # metadata proving the real pipeline ran must be present.
    assert "raw_text" in output
    assert "source_format" in output
    assert output["source_format"] == "image"
    assert "degraded" in output
