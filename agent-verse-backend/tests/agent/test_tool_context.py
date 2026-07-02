"""Tests for planner-facing agent tool context."""

from __future__ import annotations

from app.agent.tool_context import ToolContext, ToolRef


def test_tool_context_formats_tools_for_planner() -> None:
    context = ToolContext(
        connectors=[{"id": "github", "name": "GitHub"}],
        tools=[
            ToolRef(
                server_id="github",
                server_name="GitHub",
                name="list_issues",
                description="List repository issues",
                input_schema={"type": "object", "properties": {"repo": {"type": "string"}}},
            )
        ],
    )

    assert context.to_prompt_block() == (
        "Available tools:\n"
        "- GitHub.list_issues: List repository issues\n"
        '  input_schema: {"properties": {"repo": {"type": "string"}}, "type": "object"}'
    )


def test_tool_context_formats_empty_tool_list() -> None:
    assert ToolContext(connectors=[], tools=[]).to_prompt_block() == "No connector tools available."


def test_tool_context_finds_unique_tools_by_name_or_server_qualified_name() -> None:
    github_tool = ToolRef(
        server_id="github",
        server_name="GitHub",
        name="search",
        description="Search GitHub",
        input_schema={},
    )
    slack_tool = ToolRef(
        server_id="slack",
        server_name="Slack",
        name="search",
        description="Search Slack",
        input_schema={},
    )
    context = ToolContext(connectors=[], tools=[github_tool, slack_tool])

    # H-5 fix: duplicate names now return the FIRST registered tool, not None
    assert context.find_tool("search") is github_tool
    assert context.find_tool("Slack.search") is slack_tool
    assert context.find_tool("slack.search") is slack_tool
    assert context.find_tool("missing") is None


def test_tool_context_finds_unqualified_name_only_when_unambiguous() -> None:
    tool = ToolRef(
        server_id="github",
        server_name="GitHub",
        name="list_issues",
        description="List issues",
        input_schema={},
    )
    context = ToolContext(connectors=[], tools=[tool])

    assert context.find_tool("list_issues") is tool


def test_tool_context_matches_dotted_variant_of_server_name() -> None:
    tool = ToolRef(
        server_id="8ffe2effc436449689c7a6cddaa14ba3",
        server_name="PineLabs JIRA",
        name="jira_search_issues",
        description="Search Jira",
        input_schema={},
    )
    context = ToolContext(connectors=[], tools=[tool])

    assert context.find_tool("PineLabs.JIRA.jira_search_issues") is tool


def test_tool_context_matches_generic_jira_server_alias() -> None:
    tool = ToolRef(
        server_id="8ffe2effc436449689c7a6cddaa14ba3",
        server_name="PineLabs JIRA",
        name="jira_search_issues",
        description="Search Jira",
        input_schema={},
    )
    context = ToolContext(connectors=[], tools=[tool])

    assert context.find_tool("jira.jira_search_issues") is tool


def test_tool_context_matches_human_friendly_jira_search_alias() -> None:
    tool = ToolRef(
        server_id="8ffe2effc436449689c7a6cddaa14ba3",
        server_name="PineLabs JIRA",
        name="jira_search_issues",
        description="Search Jira",
        input_schema={},
    )
    context = ToolContext(connectors=[], tools=[tool])

    assert context.find_tool("Jira search") is tool


def test_tool_context_matches_dotted_jira_issue_search_alias() -> None:
    tool = ToolRef(
        server_id="8ffe2effc436449689c7a6cddaa14ba3",
        server_name="PineLabs JIRA",
        name="jira_search_issues",
        description="Search Jira",
        input_schema={},
    )
    context = ToolContext(connectors=[], tools=[tool])

    assert context.find_tool("jira.issue_search") is tool
