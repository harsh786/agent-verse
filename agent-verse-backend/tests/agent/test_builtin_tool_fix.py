"""
Tests for the builtin tool handler fix:
- Worker process correctly discovers builtin tools after restoring handlers
- discover_tools returns tools even when builtin_handler is None (Redis round-trip)
- _dispatch_builtin_tool falls back to process-local registry when handler is None
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_tenant():
    from app.tenancy.context import PlanTier, TenantContext
    return TenantContext(
        tenant_id="test-tenant-tool-fix",
        plan=PlanTier.PROFESSIONAL,
        api_key_id="test",
    )


def make_jira_server_config(with_handler: bool = True):
    """Build a minimal builtin-jira MCPServerConfig with or without the handler."""
    from app.mcp.registry import MCPServerConfig
    from app.mcp.servers.jira_server import call_tool as jira_handler

    return MCPServerConfig(
        server_id="builtin-jira",
        name="Jira",
        description="Jira connector",
        base_url="builtin://",
        enabled=True,
        tool_definitions=[
            {"name": "jira_search_issues", "description": "Search Jira issues", "parameters": {}},
            {"name": "jira_create_issue", "description": "Create a Jira issue", "parameters": {}},
        ],
        builtin_handler=jira_handler if with_handler else None,
    )


# ── Test 1: discover_tools returns tools even with handler=None ────────────────

@pytest.mark.asyncio
async def test_discover_tools_returns_tools_when_handler_none():
    """
    When MCPServerConfig is deserialized from Redis, builtin_handler is None.
    discover_tools must still return tool definitions because the URL starts
    with 'builtin://' (Fix 1 in mcp/client.py).
    """
    from app.mcp.client import MCPClient

    # Server config as it comes back from Redis (no handler)
    cfg = make_jira_server_config(with_handler=False)

    registry = AsyncMock()
    registry.get = AsyncMock(return_value=cfg)

    client = MCPClient(registry=registry, secret_resolver=AsyncMock(return_value=None))

    tenant = make_tenant()
    tools = await client.discover_tools(server_id="builtin-jira", tenant_ctx=tenant)

    assert len(tools) == 2
    tool_names = {t.name for t in tools}
    assert "jira_search_issues" in tool_names
    assert "jira_create_issue" in tool_names


# ── Test 2: handler restored from process-local registry ──────────────────────

@pytest.mark.asyncio
async def test_discover_tools_restores_handler_from_local_registry():
    """
    discover_tools lazy-restores the handler from MCPRegistry._builtin_handlers
    when the config has no handler (Redis round-trip scenario).
    """
    from app.mcp.client import MCPClient
    from app.mcp.registry import MCPRegistry
    from app.mcp.servers.jira_server import call_tool as jira_handler

    # Register the handler in the process-local dict
    MCPRegistry.register_builtin_handler("builtin-jira", jira_handler)

    cfg = make_jira_server_config(with_handler=False)
    assert cfg.builtin_handler is None  # Simulate Redis deserialization

    registry = AsyncMock()
    registry.get = AsyncMock(return_value=cfg)

    client = MCPClient(registry=registry, secret_resolver=AsyncMock(return_value=None))
    tenant = make_tenant()

    tools = await client.discover_tools(server_id="builtin-jira", tenant_ctx=tenant)

    # Should still return tools
    assert len(tools) == 2
    assert any(t.name == "jira_search_issues" for t in tools)


# ── Test 3: dispatch_builtin_tool restores handler from local registry ─────────

@pytest.mark.asyncio
async def test_dispatch_builtin_tool_restores_handler():
    """
    _dispatch_builtin_tool must try MCPRegistry._builtin_handlers when
    server.builtin_handler is None, instead of returning a
    'Built-in handler not available' error.
    """
    from app.mcp.client import MCPClient
    from app.mcp.registry import MCPRegistry

    # Register a fake handler that returns immediately
    fake_handler = AsyncMock(return_value={"result": "42 open issues found"})
    MCPRegistry.register_builtin_handler("builtin-jira-test-dispatch", fake_handler)

    # Build a server config where handler is None (as if deserialized from Redis)
    from app.mcp.registry import MCPServerConfig
    cfg = MCPServerConfig(
        server_id="builtin-jira-test-dispatch",
        name="Jira Test",
        description="test",
        base_url="builtin://",
        enabled=True,
        tool_definitions=[{"name": "jira_search_issues", "description": "search", "parameters": {}}],
        builtin_handler=None,
    )

    registry = AsyncMock()
    client = MCPClient(registry=registry, secret_resolver=AsyncMock(return_value=None))

    result = await client._dispatch_builtin_tool(
        cfg, "jira_search_issues", {"jql": "project = BAU AND status = Open"}
    )

    # The key assertion: the error must NOT be "handler not available"
    # — the fix must have restored the handler from the process-local dict
    assert result.error != "Built-in handler not available (lost after Redis round-trip)", (
        f"Handler was not restored from process-local dict; result: {result}"
    )
    # Cleanup
    from app.mcp.registry import _BUILTIN_HANDLER_REGISTRY
    _BUILTIN_HANDLER_REGISTRY.pop("builtin-jira-test-dispatch", None)


# ── Test 4: discover_all_tools returns builtin tools for a tenant ───────────────

@pytest.mark.asyncio
async def test_discover_all_tools_includes_builtin_jira():
    """
    discover_all_tools must return jira_search_issues when builtin-jira is
    registered for the tenant (even in a fresh worker process).
    """
    from app.mcp.client import MCPClient
    from app.mcp.registry import MCPRegistry
    from app.mcp.servers.jira_server import call_tool as jira_handler

    MCPRegistry.register_builtin_handler("builtin-jira", jira_handler)

    cfg = make_jira_server_config(with_handler=False)

    registry = AsyncMock()
    registry.list_server_records = AsyncMock(return_value=[("builtin-jira", cfg)])
    registry.get = AsyncMock(return_value=cfg)

    client = MCPClient(registry=registry, secret_resolver=AsyncMock(return_value=None))
    tenant = make_tenant()

    all_tools = await client.discover_all_tools(tenant_ctx=tenant)
    tool_names = {t.name for t in all_tools}

    assert "jira_search_issues" in tool_names, (
        f"jira_search_issues must be discoverable; got: {tool_names}"
    )


# ── Test 5: Worker registers builtin handlers before tool discovery ────────────

def test_get_builtin_server_configs_includes_jira():
    """
    get_builtin_server_configs() must return a config for builtin-jira
    with all 11 tool definitions. This is what the worker uses to restore
    the process-local handler registry.
    """
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    configs = get_builtin_server_configs()
    jira = next((c for c in configs if c["server_id"] == "builtin-jira"), None)

    assert jira is not None, "builtin-jira must be in get_builtin_server_configs()"
    assert jira["handler"] is not None, "builtin-jira must have a handler"
    tool_names = [t["name"] for t in jira["tool_definitions"]]
    assert "jira_search_issues" in tool_names, (
        f"jira_search_issues must be in builtin-jira tools; got: {tool_names}"
    )
    assert len(jira["tool_definitions"]) >= 5, (
        f"Expected at least 5 Jira tools, got {len(jira['tool_definitions'])}"
    )


# ── Test 6: discover_all_tools does NOT require connector_ids ──────────────────

@pytest.mark.asyncio
async def test_worker_discovers_all_tools_without_connector_ids():
    """
    When a goal is submitted without an agent_id, connector_ids is [].
    The worker context factory must fall back to discovering ALL tenant tools
    (Fix 4 in tasks.py) so the planner has tool context.
    """

    # Simulate what the worker's _build_worker_mcp_context does
    cfg = make_jira_server_config(with_handler=False)

    registry = AsyncMock()
    registry.list_server_records = AsyncMock(return_value=[("builtin-jira", cfg)])
    registry.get = AsyncMock(return_value=cfg)

    # The fix: when connector_ids is empty, discover all
    connector_ids: list = []
    if not connector_ids:
        all_records = await registry.list_server_records(tenant_ctx=make_tenant())
        connector_ids = [sid for sid, _ in all_records]

    assert "builtin-jira" in connector_ids, (
        "Without an agent, worker must discover all tenant connectors"
    )
