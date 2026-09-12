"""Comprehensive tests for app/agent/tool_calls.py — targets 90%+ statement coverage."""
from __future__ import annotations

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


def test_extract_openai_array_tool_call() -> None:
    """A model that emits the OpenAI/native tool-call array format —
    [{"name": ..., "parameters": ...}] — must be parsed (not dropped as raw text,
    which used to leak the JSON verbatim into the goal result)."""
    result = extract_tool_call('[{"name": "web_search", "parameters": {"q": "hi"}}]')
    assert result is not None
    assert result.tool == "web_search"
    assert result.arguments == {"q": "hi"}


def test_extract_double_wrapped_array_tool_call() -> None:
    """Some models double-wrap: [[{"name": ..., "parameters": ...}]]."""
    result = extract_tool_call('[[{"name": "docker_ps", "parameters": {}}]]')
    assert result is not None
    assert result.tool == "docker_ps"


def test_extract_unbalanced_array_wrapper_tool_call() -> None:
    """Some models emit an unbalanced array wrapper: '[[ {..} ]' (two opening
    brackets, one closing). json.loads fails on the whole thing, but the inner
    tool-call object is complete and MUST still be dispatched — otherwise a
    delivery step (e.g. telegram_send_message) silently never fires and the raw
    JSON leaks into the goal answer."""
    text = '[[\n\n{\n  "name": "telegram_send_message",\n  "parameters": {\n    "text": "hi"\n  }\n}\n]'
    result = extract_tool_call(text)
    assert result is not None
    assert result.tool == "telegram_send_message"
    assert result.arguments == {"text": "hi"}


def test_extract_array_wrapped_call_missing_closing_brackets() -> None:
    """A tool call wrapped in '[[' whose trailing brackets were cut off entirely
    (but whose object is complete) is still recovered."""
    text = '[[{"name": "slack_send", "parameters": {"channel": "ops", "text": "done"}}'
    result = extract_tool_call(text)
    assert result is not None
    assert result.tool == "slack_send"
    assert result.arguments == {"channel": "ops", "text": "done"}


def test_extract_plain_data_array_returns_none() -> None:
    """A JSON array of plain data records (no name+parameters shape) is a direct
    answer, not a tool call — must not be misread as one."""
    assert extract_tool_call('[{"id": 1, "title": "x"}]') is None
    assert extract_tool_call("[1, 2, 3]") is None


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
