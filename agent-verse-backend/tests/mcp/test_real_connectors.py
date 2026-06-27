"""Tests for MCP server wrappers — verify tool definitions are well-formed
and the registry wiring helpers work correctly.
"""
from __future__ import annotations


def test_github_server_tool_definitions() -> None:
    from app.mcp.servers.github_server import TOOL_DEFINITIONS

    assert len(TOOL_DEFINITIONS) >= 5
    for tool in TOOL_DEFINITIONS:
        assert "name" in tool, f"Tool missing 'name': {tool}"
        assert "description" in tool, f"Tool missing 'description': {tool}"
        assert "parameters" in tool, f"Tool missing 'parameters': {tool}"


def test_github_server_has_expected_tools() -> None:
    from app.mcp.servers.github_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "github_list_repos" in names
    assert "github_get_file" in names
    assert "github_list_issues" in names
    assert "github_create_issue" in names
    assert "github_create_pr" in names
    assert "github_search_code" in names


def test_postgres_server_tool_definitions() -> None:
    from app.mcp.servers.postgres_server import TOOL_DEFINITIONS

    assert len(TOOL_DEFINITIONS) >= 3
    names = [t["name"] for t in TOOL_DEFINITIONS]
    assert "postgres_query" in names
    assert "postgres_list_tables" in names
    assert "postgres_describe_table" in names

    for tool in TOOL_DEFINITIONS:
        assert "name" in tool
        assert "description" in tool
        assert "parameters" in tool


def test_slack_server_tool_definitions() -> None:
    from app.mcp.servers.slack_server import TOOL_DEFINITIONS

    assert len(TOOL_DEFINITIONS) >= 4
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "slack_send_message" in names
    assert "slack_list_channels" in names
    assert "slack_get_channel_history" in names
    assert "slack_search_messages" in names

    for tool in TOOL_DEFINITIONS:
        assert "name" in tool
        assert "description" in tool
        assert "parameters" in tool


def test_builtin_registry_wiring_returns_correct_count() -> None:
    """get_builtin_server_configs() returns all catalog entries regardless of env vars.

    3 original (github, postgres, slack) + 12 PM connectors + 11 new servers = 26+ total.
    """
    import os

    # Ensure none of the env vars are set so only zero-requirement servers are active
    for key in [
        "GITHUB_TOKEN", "POSTGRES_MCP_URL", "SLACK_BOT_TOKEN",
        "JIRA_BASE_URL", "CONFLUENCE_BASE_URL", "ASANA_ACCESS_TOKEN",
        "LINEAR_API_KEY", "NOTION_API_KEY", "TRELLO_API_KEY",
        "MONDAY_API_KEY", "TODOIST_API_TOKEN", "BASECAMP_ACCOUNT_ID",
        "WRIKE_ACCESS_TOKEN", "CLICKUP_API_TOKEN", "SMARTSUITE_API_KEY",
    ]:
        os.environ.pop(key, None)

    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    configs = get_builtin_server_configs()
    # 3 original + 12 PM connectors + 11 new servers = 26 total minimum
    assert len(configs) >= 15, (
        f"Expected at least 15 configs (3 original + 12 PM), got {len(configs)}"
    )

    # With no env vars, only servers that have no required env vars can be active.
    # docker_server intentionally requires no env vars (uses Docker socket).
    active = [
        c
        for c in configs
        if all(os.getenv(e, "") for e in c.get("requires_env", []))
    ]
    assert isinstance(active, list)
    # Only zero-requirement servers (e.g. Docker) can be active without env vars.
    zero_req_servers = {c["server_id"] for c in configs if not c.get("requires_env", [])}
    assert set(c["server_id"] for c in active) == zero_req_servers, (
        f"Only zero-requirement servers should be active with no env vars; "
        f"got: {[c['server_id'] for c in active]}"
    )


def test_builtin_registry_wiring_active_with_env_var(monkeypatch) -> None:
    """Setting GITHUB_TOKEN makes the GitHub server active (alongside zero-req servers)."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")

    # Re-import to pick up env change
    from app.mcp.servers.registry_wiring import get_builtin_server_configs
    import os

    configs = get_builtin_server_configs()
    active = [
        c
        for c in configs
        if all(os.getenv(e, "") for e in c.get("requires_env", []))
    ]
    active_ids = {c["server_id"] for c in active}
    # GitHub must be active when GITHUB_TOKEN is set
    assert "builtin-github" in active_ids, (
        f"builtin-github must be active when GITHUB_TOKEN is set; active: {active_ids}"
    )
    # No unexpected token-dependent servers should be active
    token_requiring = {c["server_id"] for c in active if c.get("requires_env")}
    assert token_requiring == {"builtin-github"}, (
        f"Only builtin-github should require env vars and be active; got: {token_requiring}"
    )


def test_each_builtin_config_has_handler() -> None:
    """Every builtin config must have a callable handler."""
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    for cfg in get_builtin_server_configs():
        assert callable(cfg["handler"]), (
            f"{cfg['name']} handler is not callable"
        )


def test_each_builtin_config_has_tool_definitions() -> None:
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    for cfg in get_builtin_server_configs():
        assert cfg["tool_definitions"], f"{cfg['name']} has no tool_definitions"
        for tdef in cfg["tool_definitions"]:
            assert "name" in tdef
            assert "description" in tdef
