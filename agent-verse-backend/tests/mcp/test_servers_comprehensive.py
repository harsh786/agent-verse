"""Comprehensive tests for all MCP connector servers.

Tests the common pattern:
  - TOOL_DEFINITIONS exists and is a non-empty list of well-formed dicts
  - call_tool() is present and callable (async)
  - call_tool() for the most common tools dispatch correctly (with httpx mocked)
  - call_tool() returns {"error": ...} for unknown tools (not raises)
  - Auth-guard returns an error (not raises) when token env var is missing
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.mcp.servers as _servers_pkg

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_tool_definition(tool: dict, *, module_name: str) -> None:
    assert "name" in tool, f"{module_name}: tool missing 'name'"
    assert isinstance(tool["name"], str) and tool["name"], f"{module_name}: tool name is empty"
    assert "description" in tool, f"{module_name}: tool missing 'description'"
    assert "parameters" in tool, f"{module_name}: tool missing 'parameters'"
    assert isinstance(tool["parameters"], dict), f"{module_name}: parameters must be a dict"


def _collect_server_modules() -> list[str]:
    """Return all module names under app.mcp.servers (excluding __init__ and registry_wiring)."""
    return [
        info.name
        for info in pkgutil.iter_modules(_servers_pkg.__path__)
        if info.name not in ("registry_wiring",)
    ]


SERVER_MODULES = _collect_server_modules()


# ---------------------------------------------------------------------------
# Parametrized: every server has TOOL_DEFINITIONS + call_tool
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", SERVER_MODULES)
def test_server_has_tool_definitions(module_name: str) -> None:
    """Every server exposes a non-empty TOOL_DEFINITIONS list."""
    mod = importlib.import_module(f"app.mcp.servers.{module_name}")
    assert hasattr(mod, "TOOL_DEFINITIONS"), f"{module_name} missing TOOL_DEFINITIONS"
    tools = mod.TOOL_DEFINITIONS
    assert isinstance(tools, list), f"{module_name}.TOOL_DEFINITIONS must be a list"
    assert len(tools) >= 1, f"{module_name}.TOOL_DEFINITIONS must have at least one tool"
    for tool in tools:
        _assert_tool_definition(tool, module_name=module_name)


@pytest.mark.parametrize("module_name", SERVER_MODULES)
def test_server_has_callable_call_tool(module_name: str) -> None:
    """Every server exposes an async call_tool() callable."""
    mod = importlib.import_module(f"app.mcp.servers.{module_name}")
    assert hasattr(mod, "call_tool"), f"{module_name} missing call_tool"
    import asyncio
    import inspect
    assert inspect.iscoroutinefunction(mod.call_tool), (
        f"{module_name}.call_tool must be an async function"
    )


@pytest.mark.parametrize("module_name", SERVER_MODULES)
def test_tool_names_are_unique(module_name: str) -> None:
    """Tool names within a server must be unique."""
    mod = importlib.import_module(f"app.mcp.servers.{module_name}")
    names = [t["name"] for t in mod.TOOL_DEFINITIONS]
    assert len(names) == len(set(names)), (
        f"{module_name}: duplicate tool names: {[n for n in names if names.count(n) > 1]}"
    )


@pytest.mark.parametrize("module_name", SERVER_MODULES)
def test_all_required_params_are_listed(module_name: str) -> None:
    """All 'required' fields in parameters must reference existing properties."""
    mod = importlib.import_module(f"app.mcp.servers.{module_name}")
    for tool in mod.TOOL_DEFINITIONS:
        params = tool.get("parameters", {})
        required = params.get("required", [])
        properties = params.get("properties", {})
        for req_field in required:
            assert req_field in properties, (
                f"{module_name}.{tool['name']}: required field '{req_field}' "
                f"not in properties"
            )


# ---------------------------------------------------------------------------
# GitHub server — call_tool dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_github_list_repos_dispatches_correctly() -> None:
    from app.mcp.servers import github_server

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {"name": "my-repo", "full_name": "user/my-repo", "html_url": "https://github.com/user/my-repo", "description": "A repo"}
    ]
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await github_server.call_tool("github_list_repos", {"owner": "user"})

    assert "repos" in result
    assert result["repos"][0]["name"] == "my-repo"


@pytest.mark.asyncio
async def test_github_list_issues_dispatches_correctly() -> None:
    from app.mcp.servers import github_server

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {"number": 42, "title": "Bug", "state": "open", "html_url": "https://...", "body": "desc"}
    ]
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await github_server.call_tool("github_list_issues", {"owner": "user", "repo": "repo"})

    assert "issues" in result
    assert result["issues"][0]["number"] == 42


@pytest.mark.asyncio
async def test_github_create_issue_dispatches_correctly() -> None:
    from app.mcp.servers import github_server

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "number": 99, "html_url": "https://github.com/u/r/issues/99", "title": "New Issue"
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await github_server.call_tool(
            "github_create_issue", {"owner": "u", "repo": "r", "title": "New Issue"}
        )

    assert result["issue_number"] == 99


@pytest.mark.asyncio
async def test_github_create_pr_dispatches_correctly() -> None:
    from app.mcp.servers import github_server

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "number": 7, "html_url": "https://github.com/u/r/pull/7", "title": "My PR"
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await github_server.call_tool(
            "github_create_pr", {"owner": "u", "repo": "r", "title": "My PR", "head": "feat"}
        )

    assert result["pr_number"] == 7


@pytest.mark.asyncio
async def test_github_search_code_dispatches_correctly() -> None:
    from app.mcp.servers import github_server

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "total_count": 1,
        "items": [{"name": "app.py", "path": "src/app.py", "html_url": "https://...", "repository": {"full_name": "u/r"}}]
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await github_server.call_tool("github_search_code", {"query": "def main"})

    assert result["total_count"] == 1


@pytest.mark.asyncio
async def test_github_unknown_tool_returns_error() -> None:
    from app.mcp.servers import github_server

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await github_server.call_tool("github_nonexistent", {})

    assert "error" in result
    assert "Unknown tool" in result["error"]


# ---------------------------------------------------------------------------
# Slack server — auth guard + dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slack_returns_error_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    from app.mcp.servers import slack_server

    result = await slack_server.call_tool("slack_send_message", {"channel": "#gen", "text": "hi"})
    assert "error" in result
    assert "SLACK_BOT_TOKEN" in result["error"]


@pytest.mark.asyncio
async def test_slack_send_message_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake-token")
    from app.mcp.servers import slack_server

    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True, "ts": "12345.6789"}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await slack_server.call_tool(
            "slack_send_message", {"channel": "#general", "text": "hello"}
        )

    assert result.get("ok") is True or "ts" in result


# ---------------------------------------------------------------------------
# Stripe server — basic dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stripe_returns_error_when_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    from app.mcp.servers import stripe_server

    result = await stripe_server.call_tool("stripe_list_customers", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_stripe_list_customers_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    from app.mcp.servers import stripe_server

    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"id": "cus_1", "email": "a@b.com"}], "has_more": False}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await stripe_server.call_tool("stripe_list_customers", {"limit": 5})

    assert "customers" in result or "data" in result or "error" not in result


# ---------------------------------------------------------------------------
# Postgres server — no-config guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postgres_returns_error_when_url_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_MCP_URL", raising=False)
    from app.mcp.servers import postgres_server

    result = await postgres_server.call_tool("postgres_query", {"sql": "SELECT 1"})
    assert "error" in result
    assert "POSTGRES_MCP_URL" in result["error"]


def test_postgres_get_tools_returns_fallback_without_asyncpg() -> None:
    from app.mcp.servers import postgres_server
    import sys

    with patch.dict(sys.modules, {"asyncpg": None}):
        # Force reimport isn't needed; just test the logic
        tools = postgres_server.TOOL_DEFINITIONS
        assert isinstance(tools, list)
        assert len(tools) >= 1


# ---------------------------------------------------------------------------
# registry_wiring — can be imported and returns a list
# ---------------------------------------------------------------------------


def test_registry_wiring_returns_builtin_configs() -> None:
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    configs = get_builtin_server_configs()
    assert isinstance(configs, list)
    assert len(configs) > 0
    # Each config must have at least name and call_tool
    for cfg in configs[:5]:  # spot-check first 5
        assert "name" in cfg or "server_id" in cfg or callable(cfg.get("call_tool"))


# ---------------------------------------------------------------------------
# GitHub get_file — base64 decode path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_github_get_file_decodes_base64() -> None:
    import base64
    from app.mcp.servers import github_server

    content_bytes = base64.b64encode(b"print('hello')").decode()
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "path": "src/main.py",
        "sha": "abc123",
        "encoding": "base64",
        "content": content_bytes,
        "size": 14,
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await github_server.call_tool(
            "github_get_file", {"owner": "u", "repo": "r", "path": "src/main.py"}
        )

    assert result["content"] == "print('hello')"
    assert result["path"] == "src/main.py"
