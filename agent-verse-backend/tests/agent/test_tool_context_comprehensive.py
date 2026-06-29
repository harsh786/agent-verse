"""Comprehensive tests for app/agent/tool_context.py — targets 90%+ statement coverage."""
from __future__ import annotations

import pytest

from app.agent.tool_context import ToolContext, ToolRef


# ── ToolRef ───────────────────────────────────────────────────────────────────

def test_tool_ref_construction() -> None:
    ref = ToolRef(
        server_id="srv1",
        server_name="GitHub",
        name="create_issue",
        description="Creates a GitHub issue",
        input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
    )
    assert ref.server_id == "srv1"
    assert ref.name == "create_issue"


# ── ToolContext.to_prompt_block ────────────────────────────────────────────────

def test_to_prompt_block_no_tools() -> None:
    ctx = ToolContext(connectors=[], tools=[])
    block = ctx.to_prompt_block()
    assert block == "No connector tools available."


def test_to_prompt_block_single_tool() -> None:
    ref = ToolRef(
        server_id="srv1",
        server_name="Jira",
        name="search_issues",
        description="Search Jira issues",
        input_schema={"type": "object"},
    )
    ctx = ToolContext(connectors=[{"id": "srv1"}], tools=[ref])
    block = ctx.to_prompt_block()
    assert "Available tools:" in block
    assert "Jira.search_issues" in block
    assert "Search Jira issues" in block
    assert "input_schema" in block


def test_to_prompt_block_multiple_tools() -> None:
    tools = [
        ToolRef(server_id="s1", server_name="Jira", name="get_issue", description="Get issue", input_schema={}),
        ToolRef(server_id="s2", server_name="Slack", name="send_message", description="Send msg", input_schema={}),
    ]
    ctx = ToolContext(connectors=[], tools=tools)
    block = ctx.to_prompt_block()
    assert "Jira.get_issue" in block
    assert "Slack.send_message" in block


def test_to_prompt_block_schema_sorted() -> None:
    """Schema keys should be sorted (JSON sort_keys=True)."""
    ref = ToolRef(
        server_id="s1",
        server_name="GitHub",
        name="create_pr",
        description="Create PR",
        input_schema={"z_key": "last", "a_key": "first"},
    )
    ctx = ToolContext(connectors=[], tools=[ref])
    block = ctx.to_prompt_block()
    # a_key should come before z_key
    a_pos = block.index("a_key")
    z_pos = block.index("z_key")
    assert a_pos < z_pos


# ── ToolContext.find_tool ─────────────────────────────────────────────────────

def test_find_tool_by_unqualified_name() -> None:
    ref = ToolRef(server_id="s1", server_name="GitHub", name="create_issue", description="", input_schema={})
    ctx = ToolContext(connectors=[], tools=[ref])
    found = ctx.find_tool("create_issue")
    assert found is ref


def test_find_tool_by_unqualified_name_returns_first_match() -> None:
    ref1 = ToolRef(server_id="s1", server_name="GitHub", name="list_issues", description="", input_schema={})
    ref2 = ToolRef(server_id="s2", server_name="Jira", name="list_issues", description="", input_schema={})
    ctx = ToolContext(connectors=[], tools=[ref1, ref2])
    found = ctx.find_tool("list_issues")
    assert found is ref1  # first registered


def test_find_tool_unqualified_not_found_returns_none() -> None:
    ref = ToolRef(server_id="s1", server_name="GitHub", name="other_tool", description="", input_schema={})
    ctx = ToolContext(connectors=[], tools=[ref])
    assert ctx.find_tool("nonexistent") is None


def test_find_tool_by_qualified_name_server_dot_tool() -> None:
    ref = ToolRef(server_id="s1", server_name="GitHub", name="create_issue", description="", input_schema={})
    ctx = ToolContext(connectors=[], tools=[ref])
    found = ctx.find_tool("GitHub.create_issue")
    assert found is ref


def test_find_tool_qualified_case_insensitive_server_name() -> None:
    ref = ToolRef(server_id="s1", server_name="GitHub", name="get_repo", description="", input_schema={})
    ctx = ToolContext(connectors=[], tools=[ref])
    found = ctx.find_tool("github.get_repo")
    assert found is ref


def test_find_tool_qualified_matches_server_id() -> None:
    ref = ToolRef(server_id="github-conn", server_name="GitHub Connector", name="list_prs", description="", input_schema={})
    ctx = ToolContext(connectors=[], tools=[ref])
    found = ctx.find_tool("github-conn.list_prs")
    assert found is ref


def test_find_tool_qualified_wrong_server_returns_none() -> None:
    ref = ToolRef(server_id="s1", server_name="GitHub", name="deploy", description="", input_schema={})
    ctx = ToolContext(connectors=[], tools=[ref])
    assert ctx.find_tool("Jira.deploy") is None


def test_find_tool_qualified_wrong_tool_name_returns_none() -> None:
    ref = ToolRef(server_id="s1", server_name="GitHub", name="create_issue", description="", input_schema={})
    ctx = ToolContext(connectors=[], tools=[ref])
    assert ctx.find_tool("GitHub.delete_issue") is None


def test_find_tool_empty_tools_list() -> None:
    ctx = ToolContext(connectors=[], tools=[])
    assert ctx.find_tool("any_tool") is None
    assert ctx.find_tool("Server.any_tool") is None


def test_find_tool_qualified_server_name_case_insensitive_server_id() -> None:
    """Server ID matching is also case-insensitive."""
    ref = ToolRef(server_id="GITHUB", server_name="GitHub", name="push", description="", input_schema={})
    ctx = ToolContext(connectors=[], tools=[ref])
    found = ctx.find_tool("github.push")
    assert found is ref
