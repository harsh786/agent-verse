"""MCP (Model Context Protocol) package — registry, auth, catalog, A2A."""

from app.mcp.a2a import A2ATask, A2ATaskResult, AgentCard
from app.mcp.catalog import CONNECTOR_CATALOG, ConnectorSpec
from app.mcp.registry import MCPRegistry, MCPServerConfig, ServerStatus

__all__ = [
    "CONNECTOR_CATALOG",
    "A2ATask",
    "A2ATaskResult",
    "AgentCard",
    "ConnectorSpec",
    "MCPRegistry",
    "MCPServerConfig",
    "ServerStatus",
]
