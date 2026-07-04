"""Unit tests verifying all 6 hallucination-elimination fixes."""
import pytest


# ── Vector 5: Grounded executor prompt ───────────────────────────────────────

def test_executor_system_contains_grounding_rules():
    """EXECUTOR_SYSTEM must contain all 5 grounding rules."""
    from app.agent.prompts import EXECUTOR_SYSTEM

    required_phrases = [
        "NEVER fabricate",
        "NEVER claim",
        "INSUFFICIENT DATA",
        "ONLY JSON",
        "No markdown",
    ]
    for phrase in required_phrases:
        assert phrase in EXECUTOR_SYSTEM, (
            f"EXECUTOR_SYSTEM is missing grounding rule: '{phrase}'\n"
            f"Current content:\n{EXECUTOR_SYSTEM}"
        )


# ── Vector 1: Tool name validation ───────────────────────────────────────────

def test_validate_tool_name_rejects_unknown():
    """validate_tool_name must return a rejection string for unlisted tool names."""
    from app.agent.tool_calls import validate_tool_name

    allowed = {"jira_server.jira_search_issues", "builtin-confluence.confluence_create_page"}
    result = validate_tool_name("jira_server.jira_update_sprint_velocity", allowed)

    assert result is not None, "Must return rejection message for unknown tool"
    assert "not available" in result.lower() or "unknown" in result.lower() or "not in" in result.lower(), (
        f"Rejection message must explain the tool is not available. Got: {result}"
    )
    assert "jira_update_sprint_velocity" in result, (
        "Rejection must name the bad tool"
    )


def test_validate_tool_name_accepts_known():
    """validate_tool_name must return None for tools in the allowed set."""
    from app.agent.tool_calls import validate_tool_name

    allowed = {"jira_server.jira_search_issues", "builtin-confluence.confluence_create_page"}
    result = validate_tool_name("jira_server.jira_search_issues", allowed)

    assert result is None, "Must return None when tool is known"


def test_validate_tool_name_accepts_rpa_tools():
    """validate_tool_name must always accept built-in RPA tools regardless of allowed set."""
    from app.agent.tool_calls import validate_tool_name

    allowed: set[str] = set()  # empty — no MCP tools
    result = validate_tool_name("rpa_open_url", allowed)
    assert result is None, "RPA tools must always be accepted"


# ── Vector 3: Executor context limit ─────────────────────────────────────────

def test_executor_context_limit_is_larger_than_sse_limit():
    """Executor LLM context limit must be >= 5000 chars."""
    from app.agent.sanitization import (
        _TOOL_EVENT_MAX_LENGTH,
        _EXECUTOR_CONTEXT_MAX_LENGTH,
    )
    assert _EXECUTOR_CONTEXT_MAX_LENGTH >= 5000, (
        f"Executor context limit must be >= 5000, got {_EXECUTOR_CONTEXT_MAX_LENGTH}"
    )
    assert _EXECUTOR_CONTEXT_MAX_LENGTH > _TOOL_EVENT_MAX_LENGTH, (
        "Executor context limit must be larger than SSE event limit"
    )


def test_sanitize_tool_raw_output_respects_custom_max_length():
    """sanitize_tool_raw_output must respect an explicit max_length override."""
    from app.agent.sanitization import sanitize_tool_raw_output

    long_text = "x" * 6000
    result = sanitize_tool_raw_output(long_text, max_length=5000)
    assert len(result) <= 5000 + len("...[truncated]"), (
        "Output must be capped at max_length + marker"
    )
    assert "...[truncated]" in result


def test_sanitize_tool_raw_output_uses_1000_default():
    """Default max_length is 1000 for backward compat (SSE events)."""
    from app.agent.sanitization import sanitize_tool_raw_output

    long_text = "x" * 2000
    result = sanitize_tool_raw_output(long_text)
    assert len(result) <= 1000 + len("...[truncated]")
