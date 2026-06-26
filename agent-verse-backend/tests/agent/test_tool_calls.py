"""Tests for parsing structured executor tool-call output."""

from __future__ import annotations

from app.agent.tool_calls import ToolCall, extract_tool_call


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
