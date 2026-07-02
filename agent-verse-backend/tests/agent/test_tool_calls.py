"""Tests for parsing structured executor tool-call output."""

from __future__ import annotations

from app.agent.tool_calls import ToolCall, extract_tool_call, repair_tool_call_arguments


def test_extract_tool_call_from_json_tool_arguments() -> None:
    call = extract_tool_call('{"tool": "jira_search", "arguments": {"jql": "project = BAU"}}')

    assert call == ToolCall(tool="jira_search", arguments={"jql": "project = BAU"})


def test_extract_tool_call_from_markdown_json_tool_name_args() -> None:
    call = extract_tool_call(
        '```json\n{"tool_name": "GitHub.search", "args": {"q": "bug"}}\n```'
    )

    assert call == ToolCall(tool="GitHub.search", arguments={"q": "bug"})


def test_extract_tool_call_returns_none_without_tool() -> None:
    assert extract_tool_call('{"arguments": {"jql": "project = BAU"}}') is None
    assert extract_tool_call("not json") is None


def test_extract_tool_call_uses_empty_arguments_when_arguments_absent() -> None:
    call = extract_tool_call('{"tool": "jira_search"}')

    assert call == ToolCall(tool="jira_search", arguments={})


def test_extract_tool_call_returns_none_for_list_arguments() -> None:
    assert extract_tool_call('{"tool": "rpa_screenshot", "arguments": ["bad"]}') is None


def test_extract_tool_call_returns_none_for_string_args() -> None:
    assert extract_tool_call('{"tool": "jira_search", "args": "bad"}') is None


def test_extract_tool_call_ignores_placeholder_tool_name() -> None:
    assert extract_tool_call('{"tool": "server_name.tool_name", "arguments": {}}') is None
    assert extract_tool_call('{"tool": "server_name.jira_jql", "arguments": {}}') is None
    assert extract_tool_call('{"tool": "python.datetime", "arguments": {}}') is None


def test_repair_tool_call_arguments_extracts_missing_jira_jql_from_step() -> None:
    call = ToolCall(tool="PineLabs.JIRA.jira_search_issues", arguments={})

    repaired = repair_tool_call_arguments(
        call,
        "Use Jira with the JQL "
        "'assignee = currentUser() AND created >= -26w ORDER BY created DESC'",
    )

    assert repaired.arguments == {
        "jql": "assignee = currentUser() AND created >= -26w ORDER BY created DESC"
    }


def test_repair_tool_call_arguments_replaces_placeholder_jira_jql_from_goal() -> None:
    call = ToolCall(
        tool="PineLabs.JIRA.jira_search_issues",
        arguments={"jql": "project = TEST AND status = Open", "max_results": 100},
    )

    repaired = repair_tool_call_arguments(
        call,
        "Use Jira search",
        goal="fetch all jira issues in last 6 months",
    )

    assert repaired.arguments["jql"] == "created >= -26w ORDER BY created DESC"
    assert repaired.arguments["max_results"] == 100


def test_repair_tool_call_arguments_builds_cross_project_assignee_jql() -> None:
    call = ToolCall(tool="jira_search_issues", arguments={"jql": "project = TEST"})

    repaired = repair_tool_call_arguments(
        call,
        "Use Jira search",
        goal="Find all the JIRA assigned on Abhay Dwivedi",
    )

    assert repaired.arguments["jql"] == 'assignee = "Abhay Dwivedi" ORDER BY created DESC'


def test_repair_tool_call_arguments_preserves_all_projects_for_named_assignee() -> None:
    call = ToolCall(tool="jira_search_issues", arguments={})

    repaired = repair_tool_call_arguments(
        call,
        "Search all projects for Jira assigned to Abhay Dwivedi",
    )

    assert repaired.arguments["jql"] == 'assignee = "Abhay Dwivedi" ORDER BY created DESC'


def test_repair_tool_call_arguments_maps_dotted_jira_search_alias() -> None:
    call = ToolCall(tool="jira.issue_search", arguments={"assignee": "Abhay Dwivedi"})

    repaired = repair_tool_call_arguments(
        call,
        "Execute a Jira issue search",
        goal="Find all the Jira assigned to Abhay Dwivedi",
    )

    assert repaired.tool == "jira_search_issues"
    assert repaired.arguments["jql"] == 'assignee = "Abhay Dwivedi" ORDER BY created DESC'
