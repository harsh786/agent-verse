"""Tests for the builtin-utility MCP server.

Proves the "builtin utility tools as agent-callable" mechanism: the local tool
classes (OcrDocumentTool, WebSearchTool, HttpRequestTool) are reachable BY NAME
from the agent loop's MCP surface — they appear in ``discover_all_tools`` (the
planner/executor tool list) and a ``call_tool("extract_document", ...)`` routes
through ``MCPClient._dispatch_builtin_tool`` → the utility handler →
``OcrDocumentTool`` → the one ``OcrEngine``.
"""

from __future__ import annotations

import base64
import builtins

import pytest

from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry
from app.mcp.servers import utility_server
from app.mcp.servers.registry_wiring import register_builtin_servers
from app.ocr.models import DocumentType, OcrResult
from app.tools.ocr_tool import OcrDocumentTool
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(
    tenant_id="utility-test",
    plan=PlanTier.PROFESSIONAL,
    api_key_id="utility-key-1",
)


class _FakeRedis:
    def __init__(self) -> None:
        self._d: dict[str, str] = {}
        self._s: dict[str, builtins.set[str]] = {}

    async def get(self, k: str) -> str | None:
        return self._d.get(k)

    async def set(self, k: str, v: str, ex: int | None = None) -> None:
        self._d[k] = v

    async def sadd(self, k: str, v: str) -> None:
        self._s.setdefault(k, set()).add(v)

    async def smembers(self, k: str) -> builtins.set[str]:
        return self._s.get(k, set())


class _StubOcrEngine:
    """Deterministic OcrEngine stand-in — no Tesseract / provider needed."""

    async def extract(
        self, *, image_bytes=None, pdf_bytes=None, provider=None
    ) -> OcrResult:
        return OcrResult(
            raw_text="STUB OCR TEXT",
            document_type=DocumentType.GENERAL,
            overall_confidence=0.91,
            page_count=1,
            source_format="image",
        )

    async def extract_any(
        self, data, *, content_type=None, filename=None, provider=None
    ) -> OcrResult:
        result = await self.extract(image_bytes=data)
        result.source_format = "office"
        return result


@pytest.fixture(autouse=True)
def _stub_ocr_engine():
    """Inject a deterministic OcrEngine into the utility server's OCR tool."""
    utility_server.set_tools(
        {
            "extract_document": OcrDocumentTool(ocr_engine=_StubOcrEngine()),
        }
    )
    yield
    utility_server.set_tools(None)  # reset to default lazily-built tools


def _tiny_png_b64() -> str:
    # 1x1 transparent PNG.
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    return base64.b64encode(png).decode()


async def _register_utility(registry: MCPRegistry) -> None:
    await register_builtin_servers(registry, TENANT)


@pytest.mark.asyncio
async def test_extract_document_appears_in_tenant_tool_list() -> None:
    """extract_document must show up in the tenant's discover_all_tools surface."""
    registry = MCPRegistry(redis=_FakeRedis())
    await _register_utility(registry)

    client = MCPClient(registry=registry)
    tools = await client.discover_all_tools(tenant_ctx=TENANT)

    by_name = {t.name: t for t in tools}
    assert "extract_document" in by_name, (
        f"extract_document missing from tool list: {sorted(by_name)}"
    )
    assert by_name["extract_document"].server_id == utility_server.SERVER_ID
    # The generic mechanism also exposes the other real utility tools.
    assert "web_search" in by_name
    assert "http_request" in by_name


@pytest.mark.asyncio
async def test_call_extract_document_by_name_reaches_ocr_tool() -> None:
    """A tool call to extract_document routes builtin dispatch → OcrDocumentTool."""
    registry = MCPRegistry(redis=_FakeRedis())
    await _register_utility(registry)

    client = MCPClient(registry=registry)
    result = await client.call_tool(
        server_id=utility_server.SERVER_ID,
        tool_name="extract_document",
        arguments={"image_base64": _tiny_png_b64()},
        tenant_ctx=TENANT,
    )

    assert result.success is True, f"call failed: {result.error}"
    assert result.output["raw_text"] == "STUB OCR TEXT"
    assert result.output["source_format"] == "image"
    assert "degraded" in result.output


@pytest.mark.asyncio
async def test_call_utility_handler_directly_dispatches_to_tool() -> None:
    """The handler function itself dispatches by tool_name to the right class."""
    out = await utility_server.call_tool(
        "extract_document", {"document_base64": _tiny_png_b64()}
    )
    assert out["raw_text"] == "STUB OCR TEXT"
    assert out["source_format"] == "office"  # extract_any path


@pytest.mark.asyncio
async def test_unknown_utility_tool_returns_error() -> None:
    out = await utility_server.call_tool("does_not_exist", {})
    assert "error" in out
