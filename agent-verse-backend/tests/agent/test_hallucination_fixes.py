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
