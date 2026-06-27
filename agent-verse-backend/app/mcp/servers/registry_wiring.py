"""Wire all built-in MCP server wrappers into the MCP catalog."""
from __future__ import annotations

from typing import Any


def get_builtin_server_configs() -> list[dict]:
    """Return configurations for all built-in MCP server wrappers."""
    from app.mcp.servers import github_server, postgres_server, slack_server

    return [
        {
            "server_id": "builtin-github",
            "name": "GitHub",
            "description": "GitHub repositories, issues, PRs, and code search",
            "tool_definitions": github_server.TOOL_DEFINITIONS,
            "handler": github_server.call_tool,
            "requires_env": ["GITHUB_TOKEN"],
        },
        {
            "server_id": "builtin-postgres",
            "name": "PostgreSQL",
            "description": "Query PostgreSQL databases",
            "tool_definitions": postgres_server.TOOL_DEFINITIONS,
            "handler": postgres_server.call_tool,
            "requires_env": ["POSTGRES_MCP_URL"],
        },
        {
            "server_id": "builtin-slack",
            "name": "Slack",
            "description": "Send messages and search Slack",
            "tool_definitions": slack_server.TOOL_DEFINITIONS,
            "handler": slack_server.call_tool,
            "requires_env": ["SLACK_BOT_TOKEN"],
        },
    ]


async def register_builtin_servers(registry: Any, tenant_ctx: Any) -> int:
    """Register all built-in MCP servers that have their required env vars set.

    Returns the number of servers successfully registered.
    """
    import logging
    import os

    from app.mcp.registry import MCPRegistry, MCPServerConfig

    count = 0
    for cfg in get_builtin_server_configs():
        # Only register if all required env vars are present and non-empty
        if all(os.getenv(env, "") for env in cfg.get("requires_env", [])):
            try:
                server_config = MCPServerConfig(
                    server_id=cfg["server_id"],
                    name=cfg["name"],
                    description=cfg.get("description", ""),
                    base_url="builtin://",
                    enabled=True,
                    tool_definitions=cfg["tool_definitions"],
                    builtin_handler=cfg["handler"],
                )
                await registry.register(server_config, tenant_ctx=tenant_ctx)
                # Also register handler in the process-local registry so it
                # survives Redis serialization round-trips (builtin_handler is
                # excluded from JSON).
                MCPRegistry.register_builtin_handler(cfg["server_id"], cfg["handler"])
                count += 1
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "builtin_server_register_failed: %s %s", cfg["name"], exc
                )
    return count
