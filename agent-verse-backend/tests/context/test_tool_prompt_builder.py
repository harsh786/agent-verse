"""ToolPromptBuilder — formats available tools into a per-step, prompt-ready
text block (app/context/tool_prompt_builder.py).

Only the executor path (app/agent/nodes/executor_mixin.py) calls this today,
and it only ever passes `name` + `description` per tool (input_schema /
parameters are enforced separately via the provider's native tool-use
mechanism, not echoed into this text block) — several tests below pin that
"parameters is accepted but silently ignored" behavior explicitly so a future
change to it is a deliberate decision, not an accident.
"""
from __future__ import annotations

import pytest

from app.context.tool_prompt_builder import ToolPromptBuilder


@pytest.fixture
def builder() -> ToolPromptBuilder:
    return ToolPromptBuilder()


# ── empty tool list ─────────────────────────────────────────────────────────


def test_empty_tool_list_returns_no_tools_message(builder):
    result = builder.build(tools=[], step_context="do the thing")

    assert result == "Step: do the thing\n\nNo tools available — use parametric knowledge."


def test_empty_tool_list_without_step_context_defaults_to_empty_string(builder):
    result = builder.build(tools=[])

    assert result == "Step: \n\nNo tools available — use parametric knowledge."


def test_empty_tool_list_does_not_mention_available_tools_header(builder):
    result = builder.build(tools=[])

    assert "Available tools:" not in result


# ── single tool ──────────────────────────────────────────────────────────────


def test_single_tool_with_description(builder):
    tools = [{"name": "search_web", "description": "Search the public web"}]

    result = builder.build(tools=tools, step_context="find docs")

    assert result == (
        "Step: find docs\n\n"
        "Available tools:\n"
        "  - search_web: Search the public web\n\n"
        "Use only tools listed above. Return tool call as JSON."
    )


def test_single_tool_missing_description_defaults_to_empty_string(builder):
    tools = [{"name": "search_web"}]

    result = builder.build(tools=tools, step_context="find docs")

    assert "  - search_web: \n" in result + "\n"
    assert result.endswith("Use only tools listed above. Return tool call as JSON.")


def test_single_tool_with_none_description_is_kept_as_none(builder):
    """dict.get('description', '') only substitutes when the key is *absent* —
    an explicit None is passed straight through (renders as the literal
    string "None"), which is worth pinning since it's an easy footgun for
    callers that build tool dicts from partially-populated schemas."""
    tools = [{"name": "search_web", "description": None}]

    result = builder.build(tools=tools)

    assert "  - search_web: None" in result


def test_single_tool_missing_name_raises_keyerror(builder):
    """`name` has no default fallback (unlike `description`) — a tool dict
    without it is a caller bug and should fail loudly rather than silently
    produce a malformed prompt line."""
    tools = [{"description": "no name here"}]

    with pytest.raises(KeyError):
        builder.build(tools=tools)


# ── multiple tools ───────────────────────────────────────────────────────────


def test_multiple_tools_are_each_rendered_on_their_own_line(builder):
    tools = [
        {"name": "search_web", "description": "Search the public web"},
        {"name": "read_file", "description": "Read a file from disk"},
        {"name": "send_email", "description": "Send an email"},
    ]

    result = builder.build(tools=tools, step_context="triage the ticket")

    tool_lines = result.split("Available tools:\n")[1].split("\n\nUse only")[0]
    assert tool_lines == (
        "  - search_web: Search the public web\n"
        "  - read_file: Read a file from disk\n"
        "  - send_email: Send an email"
    )


def test_multiple_tools_preserve_input_order(builder):
    tools = [{"name": f"tool_{i}", "description": f"desc {i}"} for i in range(5)]

    result = builder.build(tools=tools)

    positions = [result.index(f"tool_{i}") for i in range(5)]
    assert positions == sorted(positions)


# ── tools with complex / nested parameter schemas ────────────────────────────


def test_tool_with_nested_parameters_schema_is_accepted_but_not_rendered(builder):
    tools = [
        {
            "name": "create_deployment",
            "description": "Deploy a service",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "config": {
                        "type": "object",
                        "properties": {
                            "replicas": {"type": "integer", "minimum": 1},
                            "env": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "value": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                },
                "required": ["service"],
            },
        }
    ]

    result = builder.build(tools=tools, step_context="ship it")

    assert result == (
        "Step: ship it\n\n"
        "Available tools:\n"
        "  - create_deployment: Deploy a service\n\n"
        "Use only tools listed above. Return tool call as JSON."
    )
    # The nested schema is never echoed into the text block.
    assert "replicas" not in result
    assert "properties" not in result


def test_tool_with_extra_unknown_keys_are_ignored(builder):
    tools = [
        {
            "name": "noop",
            "description": "does nothing",
            "input_schema": {"type": "object"},
            "risk_level": "low",
            "examples": ["noop()"],
        }
    ]

    result = builder.build(tools=tools)

    assert "  - noop: does nothing" in result
    assert "risk_level" not in result
    assert "examples" not in result


# ── step_context formatting edge cases ───────────────────────────────────────


def test_step_context_with_newlines_is_passed_through_verbatim(builder):
    tools = [{"name": "t", "description": "d"}]

    result = builder.build(tools=tools, step_context="line one\nline two")

    assert result.startswith("Step: line one\nline two\n\n")


def test_tool_name_and_description_with_special_characters(builder):
    tools = [{"name": "weird:tool/name", "description": "handles <html> & \"quotes\""}]

    result = builder.build(tools=tools)

    assert '  - weird:tool/name: handles <html> & "quotes"' in result
