"""MockMCPServer — implements MCP JSON-RPC in-memory for local development and testing.

Open source, no external dependencies beyond the standard library.
Allows developers to test agent workflows without real connector deployments.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    risk: str = "read"
    fn: Callable[..., Any] | None = None
    fixture_response: Any = None


class MockMCPServer:
    """In-memory MCP server implementing JSON-RPC 2.0 protocol.

    Use in tests and local development to simulate connector behavior
    without deploying real MCP servers.
    """

    def __init__(self, server_id: str = "mock-server", name: str = "Mock Server") -> None:
        self._server_id = server_id
        self._name = name
        self._tools: dict[str, ToolSpec] = {}

    def register_tool(
        self,
        name: str,
        description: str,
        fn: Callable[..., Any] | None = None,
        fixture: Any = None,
        input_schema: dict[str, Any] | None = None,
        risk: str = "read",
    ) -> MockMCPServer:
        """Register a tool with a handler or fixture response."""
        self._tools[name] = ToolSpec(
            name=name,
            description=description,
            input_schema=input_schema or {"type": "object", "properties": {}},
            risk=risk,
            fn=fn,
            fixture_response=fixture,
        )
        return self  # Enable chaining

    def set_fixture(self, tool_name: str, response: Any) -> MockMCPServer:
        """Set a fixture response for an already-registered tool."""
        if tool_name in self._tools:
            self._tools[tool_name].fixture_response = response
        else:
            self.register_tool(tool_name, f"Mock {tool_name}", fixture=response)
        return self

    async def handle_jsonrpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Handle an MCP JSON-RPC request."""
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": t.name,
                        "description": t.description,
                        "inputSchema": t.input_schema,
                    }
                    for t in self._tools.values()
                ]
            }
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            return await self._dispatch_tool(tool_name, arguments)
        else:
            return {"error": {"code": -32601, "message": f"Method not found: {method}"}}

    async def _dispatch_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        tool = self._tools.get(tool_name)
        if tool is None:
            return {
                "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"}
            }
        # Callable handler takes precedence over fixture
        if tool.fn is not None:
            import asyncio  # noqa: F401
            import inspect
            try:
                if inspect.iscoroutinefunction(tool.fn):
                    result = await tool.fn(**arguments)
                else:
                    result = tool.fn(**arguments)
                text = result if isinstance(result, str) else json.dumps(result)
                return {"content": [{"type": "text", "text": text}]}
            except Exception as exc:
                return {"error": {"code": -32603, "message": str(exc)}}
        # Fixture response
        if tool.fixture_response is not None:
            text = (tool.fixture_response if isinstance(tool.fixture_response, str)
                    else json.dumps(tool.fixture_response))
            return {"content": [{"type": "text", "text": text}]}
        # No handler or fixture — return empty success
        return {"content": [{"type": "text", "text": f"[mock: {tool_name} called]"}]}

    def get_call_log(self) -> list[dict[str, Any]]:
        """Return all recorded tool calls (if logging enabled)."""
        return getattr(self, "_call_log", [])

    def enable_call_logging(self) -> MockMCPServer:
        """Enable recording of all tool calls for test assertions."""
        self._call_log: list[dict[str, Any]] = []
        original_dispatch = self._dispatch_tool

        async def logged_dispatch(
            tool_name: str, arguments: dict[str, Any]
        ) -> dict[str, Any]:
            result = await original_dispatch(tool_name, arguments)
            self._call_log.append({"tool": tool_name, "arguments": arguments, "result": result})
            return result

        self._dispatch_tool = logged_dispatch  # type: ignore
        return self
