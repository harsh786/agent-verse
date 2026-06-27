"""Tests for project management MCP server connectors.

Verifies that all PM servers are importable, have well-formed TOOL_DEFINITIONS,
and expose the correct call_tool interface.
"""
from __future__ import annotations

import inspect


def test_all_pm_servers_importable() -> None:
    from app.mcp.servers import (
        jira_server,
        confluence_server,
        asana_server,
        linear_server,
        notion_server,
        trello_server,
        monday_server,
        todoist_server,
    )

    for s in [
        jira_server,
        confluence_server,
        asana_server,
        linear_server,
        notion_server,
        trello_server,
        monday_server,
        todoist_server,
    ]:
        assert hasattr(s, "TOOL_DEFINITIONS") and len(s.TOOL_DEFINITIONS) >= 5, (
            f"{s.__name__} must have TOOL_DEFINITIONS with at least 5 tools, "
            f"got {len(getattr(s, 'TOOL_DEFINITIONS', []))}"
        )
        assert hasattr(s, "call_tool"), f"{s.__name__} must expose a call_tool function"
        assert callable(s.call_tool), f"{s.__name__}.call_tool must be callable"


def test_bonus_pm_servers_importable() -> None:
    """Bonus servers: basecamp, wrike, clickup, smartsuite."""
    from app.mcp.servers import (
        basecamp_server,
        wrike_server,
        clickup_server,
        smartsuite_server,
    )

    for s in [basecamp_server, wrike_server, clickup_server, smartsuite_server]:
        assert hasattr(s, "TOOL_DEFINITIONS") and len(s.TOOL_DEFINITIONS) >= 5
        assert hasattr(s, "call_tool")
        assert callable(s.call_tool)


def test_tool_definitions_well_formed() -> None:
    """Every tool in every PM server must have name, description, and parameters."""
    from app.mcp.servers import (
        jira_server,
        confluence_server,
        asana_server,
        linear_server,
        notion_server,
        trello_server,
        monday_server,
        todoist_server,
        basecamp_server,
        wrike_server,
        clickup_server,
        smartsuite_server,
    )

    servers = [
        jira_server, confluence_server, asana_server, linear_server,
        notion_server, trello_server, monday_server, todoist_server,
        basecamp_server, wrike_server, clickup_server, smartsuite_server,
    ]

    for server in servers:
        for tool in server.TOOL_DEFINITIONS:
            assert "name" in tool, f"Tool in {server.__name__} missing 'name': {tool}"
            assert "description" in tool, f"Tool {tool.get('name')} in {server.__name__} missing 'description'"
            assert "parameters" in tool, f"Tool {tool.get('name')} in {server.__name__} missing 'parameters'"
            assert tool["parameters"]["type"] == "object", (
                f"Tool {tool.get('name')} parameters must have type: object"
            )


def test_jira_has_transition_tool() -> None:
    from app.mcp.servers.jira_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "jira_transition_issue" in names
    assert "jira_create_issue" in names
    assert "jira_add_comment" in names
    assert "jira_get_transitions" in names
    assert "jira_assign_issue" in names
    assert "jira_list_projects" in names
    assert "jira_search_issues" in names
    assert "jira_get_issue" in names
    assert "jira_update_issue" in names
    assert "jira_create_sprint" in names
    assert "jira_get_board_sprints" in names


def test_notion_has_database_query() -> None:
    from app.mcp.servers.notion_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "notion_query_database" in names
    assert "notion_get_database" in names
    assert "notion_create_database" in names
    assert "notion_append_blocks" in names
    assert "notion_search" in names
    assert "notion_get_page" in names
    assert "notion_create_page" in names
    assert "notion_update_page" in names


def test_linear_uses_graphql() -> None:
    from app.mcp.servers import linear_server

    src = inspect.getsource(linear_server)
    assert "graphql" in src.lower() or "api.linear.app" in src
    assert "LINEAR_GQL" in src


def test_confluence_has_search_and_attach() -> None:
    from app.mcp.servers.confluence_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "confluence_search" in names
    assert "confluence_attach_file" in names
    assert "confluence_create_page" in names
    assert "confluence_update_page" in names
    assert "confluence_list_spaces" in names
    assert "confluence_add_comment" in names


def test_trello_has_move_and_checklist() -> None:
    from app.mcp.servers.trello_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "trello_move_card" in names
    assert "trello_create_checklist" in names
    assert "trello_archive_card" in names


def test_monday_uses_graphql() -> None:
    from app.mcp.servers import monday_server

    src = inspect.getsource(monday_server)
    assert "api.monday.com" in src
    assert "mutation" in src.lower() or "query" in src.lower()


def test_asana_has_workspace_support() -> None:
    from app.mcp.servers.asana_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "asana_list_tasks" in names
    assert "asana_create_task" in names
    assert "asana_list_projects" in names
    assert "asana_add_comment" in names

    # workspace_gid should appear in list_projects or create_project
    wp_tools = [t for t in TOOL_DEFINITIONS if "workspace_gid" in str(t)]
    assert len(wp_tools) >= 1, "At least one tool should accept workspace_gid"


def test_todoist_has_close_task() -> None:
    from app.mcp.servers.todoist_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "todoist_close_task" in names
    assert "todoist_list_projects" in names
    assert "todoist_create_project" in names


def test_clickup_has_workspace_and_spaces() -> None:
    from app.mcp.servers.clickup_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "clickup_list_workspaces" in names
    assert "clickup_list_spaces" in names
    assert "clickup_create_task" in names
    assert "clickup_add_comment" in names


def test_smartsuite_has_crud() -> None:
    from app.mcp.servers.smartsuite_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "smartsuite_list_records" in names
    assert "smartsuite_create_record" in names
    assert "smartsuite_update_record" in names
    assert "smartsuite_delete_record" in names


def test_wrike_has_spaces_and_contacts() -> None:
    from app.mcp.servers.wrike_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "wrike_list_spaces" in names
    assert "wrike_list_contacts" in names
    assert "wrike_create_task" in names
    assert "wrike_add_comment" in names


def test_basecamp_has_todos_and_messages() -> None:
    from app.mcp.servers.basecamp_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "basecamp_create_todo" in names
    assert "basecamp_create_message" in names
    assert "basecamp_list_projects" in names
    assert "basecamp_complete_todo" in names


def test_pm_servers_in_registry_wiring() -> None:
    """All new PM servers must be present in get_builtin_server_configs."""
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    configs = get_builtin_server_configs()
    server_ids = {c["server_id"] for c in configs}

    expected = {
        "builtin-jira",
        "builtin-confluence",
        "builtin-asana",
        "builtin-linear",
        "builtin-notion",
        "builtin-trello",
        "builtin-monday",
        "builtin-todoist",
        "builtin-basecamp",
        "builtin-wrike",
        "builtin-clickup",
        "builtin-smartsuite",
    }
    for expected_id in expected:
        assert expected_id in server_ids, f"{expected_id} missing from registry wiring"


def test_registry_wiring_total_count() -> None:
    """Total configs should now include original 3 plus 12 new PM servers."""
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    configs = get_builtin_server_configs()
    assert len(configs) >= 15, f"Expected at least 15 configs, got {len(configs)}"
