"""Comprehensive tests for app/agent/tool_calls.py — targets 90%+ statement coverage."""
from __future__ import annotations

import pytest

from app.agent.tool_calls import ToolCall, extract_tool_call


# ── ToolCall dataclass ─────────────────────────────────────────────────────────

def test_tool_call_construction() -> None:
    tc = ToolCall(tool="jira_get", arguments={"project": "PROJ"})
    assert tc.tool == "jira_get"
    assert tc.arguments == {"project": "PROJ"}


def test_tool_call_empty_args() -> None:
    tc = ToolCall(tool="list_issues", arguments={})
    assert tc.arguments == {}


# ── extract_tool_call — valid JSON ────────────────────────────────────────────

def test_extract_plain_json_with_tool_key() -> None:
    text = '{"tool": "search_issues", "arguments": {"query": "open bugs"}}'
    result = extract_tool_call(text)
    assert result is not None
    assert result.tool == "search_issues"
    assert result.arguments == {"query": "open bugs"}


def test_extract_plain_json_with_tool_name_key() -> None:
    text = '{"tool_name": "create_ticket", "arguments": {"title": "Bug #1"}}'
    result = extract_tool_call(text)
    assert result is not None
    assert result.tool == "create_ticket"


def test_extract_with_args_key_instead_of_arguments() -> None:
    text = '{"tool": "deploy", "args": {"env": "staging"}}'
    result = extract_tool_call(text)
    assert result is not None
    assert result.arguments == {"env": "staging"}


def test_extract_no_arguments_key_returns_empty_dict() -> None:
    text = '{"tool": "list_repos"}'
    result = extract_tool_call(text)
    assert result is not None
    assert result.arguments == {}


# ── extract_tool_call — markdown fenced block ────────────────────────────────

def test_extract_from_markdown_json_fence() -> None:
    text = '```json\n{"tool": "fetch_data", "arguments": {"id": 42}}\n```'
    result = extract_tool_call(text)
    assert result is not None
    assert result.tool == "fetch_data"
    assert result.arguments == {"id": 42}


def test_extract_from_plain_code_fence() -> None:
    text = '```\n{"tool": "run_query", "arguments": {"sql": "SELECT 1"}}\n```'
    result = extract_tool_call(text)
    assert result is not None
    assert result.tool == "run_query"


def test_extract_tool_name_cast_to_string() -> None:
    """tool value is always cast to str."""
    text = '{"tool": "my_tool", "arguments": {}}'
    result = extract_tool_call(text)
    assert isinstance(result.tool, str)


# ── extract_tool_call — failure paths ────────────────────────────────────────

def test_extract_invalid_json_returns_none() -> None:
    result = extract_tool_call("not json at all")
    assert result is None


def test_extract_json_array_returns_none() -> None:
    result = extract_tool_call('[{"tool": "test"}]')
    assert result is None


def test_extract_missing_tool_key_returns_none() -> None:
    result = extract_tool_call('{"action": "fetch", "arguments": {}}')
    assert result is None


def test_extract_null_tool_returns_none() -> None:
    result = extract_tool_call('{"tool": null, "arguments": {}}')
    assert result is None


def test_extract_empty_tool_string_returns_none() -> None:
    result = extract_tool_call('{"tool": "", "arguments": {}}')
    assert result is None


def test_extract_arguments_not_dict_returns_none() -> None:
    result = extract_tool_call('{"tool": "do_thing", "arguments": ["not", "a", "dict"]}')
    assert result is None


def test_extract_empty_string_returns_none() -> None:
    result = extract_tool_call("")
    assert result is None


def test_extract_whitespace_only_returns_none() -> None:
    result = extract_tool_call("   ")
    assert result is None


def test_extract_nested_json_in_arguments() -> None:
    text = '{"tool": "complex", "arguments": {"nested": {"key": "value"}, "list": [1, 2, 3]}}'
    result = extract_tool_call(text)
    assert result is not None
    assert result.arguments["nested"]["key"] == "value"
    assert result.arguments["list"] == [1, 2, 3]


def test_extract_strips_whitespace_from_text() -> None:
    text = '   \n  {"tool": "trim_me", "arguments": {}}\n   '
    result = extract_tool_call(text)
    assert result is not None
    assert result.tool == "trim_me"


def test_extract_args_not_dict_returns_none() -> None:
    text = '{"tool": "t", "args": "not a dict"}'
    result = extract_tool_call(text)
    assert result is None
