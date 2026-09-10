"""Built-in *utility* MCP server — makes AgentVerse's own local tool classes
agent-callable BY NAME from the agent loop.

Unlike the external connector servers in this package (``github_server``,
``slack_server``, …) which wrap third-party HTTP APIs, this server exposes the
in-process utility tool classes under ``app/tools/`` as MCP tools. Registering
it via :func:`app.mcp.servers.registry_wiring.get_builtin_server_configs` (with
no ``requires_env``) puts these tools on **every** tenant's builtin surface, so
the planner/executor can discover them in ``tools/list`` and dispatch a call to
e.g. ``extract_document`` straight through ``MCPClient._dispatch_builtin_tool``
to :class:`app.tools.ocr_tool.OcrDocumentTool` and the one ``OcrEngine`` — no
new OCR logic, no duplication.

Only tools backed by a genuinely-implemented local class are registered:
``extract_document`` (OCR), ``web_search`` and ``http_request``. Catalog
metadata entries with no real local impl (e.g. ``pdf_generator``,
``audio_transcriber``) are deliberately NOT exposed here.
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger
from app.tools.http_tool import HttpRequestTool
from app.tools.ocr_tool import OcrDocumentTool
from app.tools.web_search import WebSearchTool

logger = get_logger(__name__)

# Stable, human-readable server identity (mirrors the "builtin-<x>" convention).
SERVER_ID = "builtin-utility"
SERVER_NAME = "Utility Tools"
SERVER_DESCRIPTION = (
    "AgentVerse built-in utility tools: universal document OCR/extraction, "
    "web search, and outbound HTTP requests."
)

# MCP tool definitions advertised to the agent's tools/list. Each ``name`` maps
# 1:1 to a local tool class in ``app/tools/`` (see _build_tools). Keeping the
# schemas here — rather than duplicating field logic — means dispatch stays a
# thin ``tool.execute(**arguments)`` call.
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "extract_document",
        "description": OcrDocumentTool.description,
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Local filesystem path to an image or PDF.",
                },
                "image_base64": {
                    "type": "string",
                    "description": "Base64-encoded image bytes.",
                },
                "pdf_base64": {
                    "type": "string",
                    "description": "Base64-encoded PDF bytes.",
                },
                "document_base64": {
                    "type": "string",
                    "description": (
                        "Base64-encoded bytes of any document format (office docs, "
                        "etc.) routed through universal OCR."
                    ),
                },
                "content_type": {
                    "type": "string",
                    "description": "MIME type hint for document_base64.",
                },
                "filename": {
                    "type": "string",
                    "description": "Original filename hint for document_base64.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "web_search",
        "description": WebSearchTool.description,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "num_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return.",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "http_request",
        "description": HttpRequestTool.description,
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute URL to request (external hosts only).",
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                    "default": "GET",
                },
                "headers": {
                    "type": "object",
                    "description": "Optional request headers.",
                },
                "body": {
                    "description": "Optional request body (object → JSON, string → raw).",
                },
                "timeout": {
                    "type": "number",
                    "description": "Optional timeout in seconds (capped server-side).",
                },
            },
            "required": ["url"],
        },
    },
]


# Lazily-built singleton tool instances. Construction is deferred so importing
# this module never eagerly builds an OcrEngine (Tesseract/classifier); tests
# override the map via :func:`set_tools`.
_tools: dict[str, Any] | None = None


def _build_tools() -> dict[str, Any]:
    return {
        "extract_document": OcrDocumentTool(),
        "web_search": WebSearchTool(),
        "http_request": HttpRequestTool(),
    }


def _get_tools() -> dict[str, Any]:
    global _tools
    if _tools is None:
        _tools = _build_tools()
    return _tools


def set_tools(tools: dict[str, Any] | None) -> None:
    """Override (or, with ``None``, reset to lazily-built defaults) the tool map.

    Test hook used to inject deterministic tool instances (e.g. an
    ``OcrDocumentTool`` wrapping a stub ``OcrEngine``).
    """
    global _tools
    _tools = tools


async def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Dispatch an agent tool call to the matching local utility tool class.

    Signature matches the built-in server contract expected by
    ``MCPClient._dispatch_builtin_tool`` (``handler(tool_name, arguments,
    credentials=...)``). ``credentials`` is accepted for contract parity but the
    utility tools are self-contained (env-configured) and do not use it.

    Returns a plain dict on every path — invalid arguments or tool failures come
    back as ``{"error": ...}`` (never a raised exception), matching the built-in
    connector contract; ``_dispatch_builtin_tool`` maps that to a failed result.
    """
    tools = _get_tools()
    # Explicit per-tool dispatch. Each name maps 1:1 to a local tool class built
    # in ``_build_tools``; naming them here keeps the dispatch surface auditable.
    if tool_name == "extract_document":
        tool = tools["extract_document"]
    elif tool_name == "web_search":
        tool = tools["web_search"]
    elif tool_name == "http_request":
        tool = tools["http_request"]
    else:
        return {"error": f"Unknown utility tool: {tool_name!r}"}

    try:
        return await tool.execute(**(arguments or {}))
    except Exception as exc:
        # Surface failures as data, never raise to the caller (builtin contract).
        return {"error": f"{tool_name} failed: {exc}"}
