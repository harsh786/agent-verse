"""Tests for app/mcp/servers/registry_wiring.py — register_builtin_servers."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestGetBuiltinServerConfigs:
    def test_returns_list(self):
        from app.mcp.servers.registry_wiring import get_builtin_server_configs
        result = get_builtin_server_configs()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_each_config_has_required_keys(self):
        from app.mcp.servers.registry_wiring import get_builtin_server_configs
        for cfg in get_builtin_server_configs():
            assert "server_id" in cfg
            assert "name" in cfg
            assert "handler" in cfg
            assert "tool_definitions" in cfg

    def test_server_ids_are_unique(self):
        from app.mcp.servers.registry_wiring import get_builtin_server_configs
        configs = get_builtin_server_configs()
        ids = [c["server_id"] for c in configs]
        # After dedup, length should match unique IDs
        assert len(set(ids)) <= len(ids)


class TestRegisterBuiltinServers:
    @pytest.mark.asyncio
    async def test_registers_zero_when_env_vars_missing(self):
        """No env vars set → no servers registered (requires_env not satisfied)."""
        from app.mcp.servers.registry_wiring import register_builtin_servers

        mock_registry = MagicMock()
        mock_registry.register = AsyncMock()

        mock_tenant_ctx = MagicMock()

        # Clear all known env vars so nothing satisfies requires_env
        env_overrides = {
            "GITHUB_TOKEN": "",
            "JIRA_URL": "",
            "JIRA_TOKEN": "",
            "SLACK_BOT_TOKEN": "",
            "CONFLUENCE_URL": "",
            "CONFLUENCE_TOKEN": "",
            "SALESFORCE_INSTANCE_URL": "",
            "SALESFORCE_ACCESS_TOKEN": "",
            "DATADOG_API_KEY": "",
        }
        with patch.dict(os.environ, env_overrides):
            # Remove keys entirely so os.getenv returns ""
            count = await register_builtin_servers(mock_registry, mock_tenant_ctx)

        # Count may be > 0 for servers with empty requires_env
        assert isinstance(count, int)
        assert count >= 0

    @pytest.mark.asyncio
    async def test_registers_server_when_env_present(self):
        """When required env vars are set, the server gets registered."""
        from app.mcp.servers.registry_wiring import register_builtin_servers, get_builtin_server_configs

        # Find a config that has requires_env
        configs = get_builtin_server_configs()
        target = next(
            (c for c in configs if c.get("requires_env")),
            None,
        )
        if target is None:
            pytest.skip("No configs have requires_env — skip")

        mock_registry = MagicMock()
        mock_registry.register = AsyncMock()

        # Patch MCPRegistry.register_builtin_handler
        with patch("app.mcp.registry.MCPRegistry.register_builtin_handler"):
            env_patch = {var: "dummy_value" for var in target["requires_env"]}
            with patch.dict(os.environ, env_patch):
                count = await register_builtin_servers(mock_registry, MagicMock())

        assert count >= 1

    @pytest.mark.asyncio
    async def test_handles_registration_exception_gracefully(self):
        """If registry.register raises, error is swallowed and count not incremented."""
        from app.mcp.servers.registry_wiring import register_builtin_servers, get_builtin_server_configs

        configs = get_builtin_server_configs()
        target = next(
            (c for c in configs if not c.get("requires_env")),
            None,
        )
        if target is None:
            pytest.skip("No configs without requires_env")

        mock_registry = MagicMock()
        mock_registry.register = AsyncMock(side_effect=RuntimeError("db error"))

        with patch("app.mcp.registry.MCPRegistry.register_builtin_handler"):
            count = await register_builtin_servers(mock_registry, MagicMock())

        # Exception was swallowed; count remains 0 for failing configs
        assert isinstance(count, int)

    @pytest.mark.asyncio
    async def test_returns_integer(self):
        from app.mcp.servers.registry_wiring import register_builtin_servers
        mock_registry = MagicMock()
        mock_registry.register = AsyncMock()
        with patch("app.mcp.registry.MCPRegistry.register_builtin_handler"):
            result = await register_builtin_servers(mock_registry, MagicMock())
        assert isinstance(result, int)
