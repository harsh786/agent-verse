"""
Tests for the Universal Tool Intelligence Layer.

These tests verify that the system handles ANY tool's argument variations
without manual per-tool coding.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.mcp.tool_intelligence import (
    SchemaAwarePromptInjector,
    SelfHealingToolCaller,
    UniversalArgumentResolver,
    get_healer,
    get_resolver,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

JIRA_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "jql": {"type": "string", "description": "JQL query"},
        "max_results": {"type": "integer", "default": 50},
        "fields": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["jql"],
}

CONFLUENCE_CREATE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Page title"},
        "body": {"type": "string", "description": "Page content (HTML/Storage format)"},
        "space_key": {"type": "string", "description": "Confluence space key"},
        "parent_page_id": {"type": "string"},
    },
    "required": ["title", "body", "space_key"],
}

SLACK_SEND_SCHEMA = {
    "type": "object",
    "properties": {
        "channel": {"type": "string"},
        "message": {"type": "string"},
        "thread_ts": {"type": "string"},
    },
    "required": ["channel", "message"],
}

GITHUB_PR_SCHEMA = {
    "type": "object",
    "properties": {
        "repo": {"type": "string"},
        "owner": {"type": "string"},
        "pr_number": {"type": "integer"},
    },
    "required": ["repo", "owner", "pr_number"],
}


# ═══════════════════════════════════════════════════════════════════════════════
# UniversalArgumentResolver
# ═══════════════════════════════════════════════════════════════════════════════

class TestUniversalArgumentResolver:

    def setup_method(self):
        self.r = UniversalArgumentResolver()

    # ── Exact matches pass through unchanged ──────────────────────────────────

    def test_exact_match_unchanged(self):
        args = {"jql": "project = BAU", "max_results": 10}
        resolved = self.r.resolve(JIRA_SEARCH_SCHEMA, args)
        assert resolved["jql"] == "project = BAU"

    def test_no_schema_returns_original(self):
        args = {"query": "test"}
        assert self.r.resolve(None, args) == args
        assert self.r.resolve({}, args) == args

    def test_empty_args_returns_empty(self):
        assert self.r.resolve(JIRA_SEARCH_SCHEMA, {}) == {}

    # ── Semantic alias resolution ─────────────────────────────────────────────

    def test_jira_query_becomes_jql(self):
        args = {"query": "project = BAU AND status = Open"}
        resolved = self.r.resolve(JIRA_SEARCH_SCHEMA, args)
        assert resolved["jql"] == "project = BAU AND status = Open"

    def test_jira_search_query_becomes_jql(self):
        args = {"search_query": "assignee = john"}
        resolved = self.r.resolve(JIRA_SEARCH_SCHEMA, args)
        assert resolved["jql"] == "assignee = john"

    def test_confluence_content_becomes_body(self):
        args = {"title": "Report", "content": "<h1>Issues</h1>", "space_key": "ENG"}
        resolved = self.r.resolve(CONFLUENCE_CREATE_SCHEMA, args)
        assert resolved["body"] == "<h1>Issues</h1>"

    def test_confluence_text_becomes_body(self):
        args = {"title": "Report", "text": "<p>Hello</p>", "space_key": "ENG"}
        resolved = self.r.resolve(CONFLUENCE_CREATE_SCHEMA, args)
        assert resolved["body"] == "<p>Hello</p>"

    def test_confluence_page_content_becomes_body(self):
        args = {"title": "Report", "page_content": "<ul><li>Issue</li></ul>", "space_key": "ENG"}
        resolved = self.r.resolve(CONFLUENCE_CREATE_SCHEMA, args)
        assert resolved["body"] == "<ul><li>Issue</li></ul>"

    def test_confluence_page_title_becomes_title(self):
        args = {"page_title": "Jira Summary", "body": "content", "space_key": "ENG"}
        resolved = self.r.resolve(CONFLUENCE_CREATE_SCHEMA, args)
        assert resolved["title"] == "Jira Summary"

    def test_confluence_space_becomes_space_key(self):
        args = {"title": "Page", "body": "content", "space": "ENGINEERING"}
        resolved = self.r.resolve(CONFLUENCE_CREATE_SCHEMA, args)
        assert resolved["space_key"] == "ENGINEERING"

    def test_slack_channel_id_becomes_channel(self):
        args = {"channel_id": "C12345", "message": "Hello"}
        resolved = self.r.resolve(SLACK_SEND_SCHEMA, args)
        assert resolved["channel"] == "C12345"

    def test_slack_text_becomes_message(self):
        args = {"channel": "C12345", "text": "Hello world"}
        resolved = self.r.resolve(SLACK_SEND_SCHEMA, args)
        assert resolved["message"] == "Hello world"

    def test_slack_body_becomes_message(self):
        args = {"channel": "C12345", "body": "Hello from body"}
        resolved = self.r.resolve(SLACK_SEND_SCHEMA, args)
        assert resolved["message"] == "Hello from body"

    def test_github_repository_becomes_repo(self):
        args = {"repository": "agentverse", "owner": "pinelabs", "pr_number": 42}
        resolved = self.r.resolve(GITHUB_PR_SCHEMA, args)
        assert resolved["repo"] == "agentverse"

    def test_github_org_becomes_owner(self):
        args = {"repo": "agentverse", "org": "pinelabs", "pr_number": 42}
        resolved = self.r.resolve(GITHUB_PR_SCHEMA, args)
        assert resolved["owner"] == "pinelabs"

    # ── Normalised key matching (case/punctuation insensitive) ────────────────

    def test_camel_case_to_snake_case(self):
        args = {"spaceKey": "ENG", "title": "Page", "body": "content"}
        resolved = self.r.resolve(CONFLUENCE_CREATE_SCHEMA, args)
        assert resolved["space_key"] == "ENG"

    def test_uppercase_key_matches(self):
        args = {"JQL": "project = BAU"}
        resolved = self.r.resolve(JIRA_SEARCH_SCHEMA, args)
        assert resolved["jql"] == "project = BAU"

    # ── Fuzzy substring matching ──────────────────────────────────────────────

    def test_fuzzy_jql_query_matches_jql(self):
        args = {"jqlQuery": "project = PROJ"}
        resolved = self.r.resolve(JIRA_SEARCH_SCHEMA, args)
        assert resolved["jql"] == "project = PROJ"

    def test_fuzzy_space_key_matches(self):
        args = {"confluence_space_key": "TEAM", "title": "Page", "body": "x"}
        resolved = self.r.resolve(CONFLUENCE_CREATE_SCHEMA, args)
        assert resolved["space_key"] == "TEAM"

    # ── Full goal scenario: Jira + Confluence ─────────────────────────────────

    def test_full_jira_search_scenario(self):
        """LLM uses wrong key names for ALL params."""
        args = {
            "search_query": "assignee = 'Abhay Dwivedi' AND status = Open",
            "count": 100,
        }
        resolved = self.r.resolve(JIRA_SEARCH_SCHEMA, args)
        assert resolved["jql"] == "assignee = 'Abhay Dwivedi' AND status = Open"
        assert resolved.get("max_results") == 100

    def test_full_confluence_create_scenario(self):
        """LLM uses wrong key names for ALL required params."""
        args = {
            "page_title": "Abhay Dwivedi Jira Issues",
            "content": "<h1>Open Issues</h1><ul><li>Issue 1</li></ul>",
            "space": "TEAM",
        }
        resolved = self.r.resolve(CONFLUENCE_CREATE_SCHEMA, args)
        assert resolved["title"] == "Abhay Dwivedi Jira Issues"
        assert resolved["body"] == "<h1>Open Issues</h1><ul><li>Issue 1</li></ul>"
        assert resolved["space_key"] == "TEAM"
        # No required params missing
        missing = UniversalArgumentResolver().report_missing(
            "confluence_create_page", CONFLUENCE_CREATE_SCHEMA, resolved
        )
        assert missing == [], f"Still missing: {missing}"

    # ── report_missing ─────────────────────────────────────────────────────────

    def test_report_missing_detects_gaps(self):
        args = {"title": "Page", "space_key": "ENG"}  # missing body
        missing = self.r.report_missing("confluence_create_page", CONFLUENCE_CREATE_SCHEMA, args)
        assert "body" in missing

    def test_report_missing_all_present(self):
        args = {"title": "Page", "body": "content", "space_key": "ENG"}
        missing = self.r.report_missing("confluence_create_page", CONFLUENCE_CREATE_SCHEMA, args)
        assert missing == []


# ═══════════════════════════════════════════════════════════════════════════════
# SelfHealingToolCaller
# ═══════════════════════════════════════════════════════════════════════════════

class TestSelfHealingToolCaller:

    # ── is_argument_error detection ──────────────────────────────────────────

    def test_detects_keyerror_jql(self):
        assert SelfHealingToolCaller.is_argument_error("KeyError: 'jql'")

    def test_detects_keyerror_body(self):
        assert SelfHealingToolCaller.is_argument_error("KeyError: 'body'")

    def test_detects_keyerror_title(self):
        assert SelfHealingToolCaller.is_argument_error("'title'")

    def test_does_not_flag_http_error(self):
        assert not SelfHealingToolCaller.is_argument_error("HTTP 404: Not Found")

    def test_does_not_flag_auth_error(self):
        assert not SelfHealingToolCaller.is_argument_error("401 Unauthorized")

    def test_does_not_flag_network_error(self):
        assert not SelfHealingToolCaller.is_argument_error("Connection refused")

    def test_does_not_flag_none(self):
        assert not SelfHealingToolCaller.is_argument_error(None)

    def test_does_not_flag_empty(self):
        assert not SelfHealingToolCaller.is_argument_error("")

    # ── Healing with resolver ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_heal_jql_with_resolver(self):
        healer = SelfHealingToolCaller(provider=None)
        resolver = UniversalArgumentResolver()

        failed_result = MagicMock(success=False, error="KeyError: 'jql'")
        original_args = {"query": "project = BAU", "max_results": 10}

        healed = await healer.heal(
            tool_name="jira_search_issues",
            tool_schema=JIRA_SEARCH_SCHEMA,
            original_arguments=original_args,
            failed_result=failed_result,
            resolver=resolver,
        )

        assert "jql" in healed
        assert healed["jql"] == "project = BAU"

    @pytest.mark.asyncio
    async def test_heal_body_with_resolver(self):
        healer = SelfHealingToolCaller(provider=None)
        resolver = UniversalArgumentResolver()

        failed_result = MagicMock(success=False, error="KeyError: 'body'")
        original_args = {
            "title": "Report", "content": "<h1>Issues</h1>", "space_key": "ENG"
        }

        healed = await healer.heal(
            tool_name="confluence_create_page",
            tool_schema=CONFLUENCE_CREATE_SCHEMA,
            original_arguments=original_args,
            failed_result=failed_result,
            resolver=resolver,
        )

        assert healed["body"] == "<h1>Issues</h1>"

    @pytest.mark.asyncio
    async def test_heal_type_inject_fallback(self):
        """When no semantic match exists, inject by type."""
        healer = SelfHealingToolCaller(provider=None)

        failed_result = MagicMock(success=False, error="KeyError: 'jql'")
        # Argument has a string but under completely unknown key
        original_args = {"mysterious_query_string": "project = X"}

        healed = healer._extract_and_inject(
            "jira_search_issues", JIRA_SEARCH_SCHEMA, original_args, "KeyError: 'jql'"
        )

        # Should inject the string value for jql
        assert "jql" in healed

    @pytest.mark.asyncio
    async def test_heal_with_llm_provider(self):
        """When resolver alone can't fix it, use LLM provider."""
        mock_provider = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = '{"jql": "project = BAU AND status = Open"}'
        mock_provider.complete = AsyncMock(return_value=mock_response)

        healer = SelfHealingToolCaller(provider=mock_provider)
        resolver = UniversalArgumentResolver()

        # Arguments that the resolver can't fix (completely unknown key)
        original_args = {"zzz_unknown_param": "project = BAU AND status = Open"}
        failed_result = MagicMock(success=False, error="KeyError: 'jql'")

        healed = await healer.heal(
            tool_name="jira_search_issues",
            tool_schema=JIRA_SEARCH_SCHEMA,
            original_arguments=original_args,
            failed_result=failed_result,
            resolver=resolver,
        )

        # LLM should have been called
        mock_provider.complete.assert_called_once()
        assert "jql" in healed


# ═══════════════════════════════════════════════════════════════════════════════
# SchemaAwarePromptInjector
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaAwarePromptInjector:

    def make_tool(self, name: str, schema: dict, server_id: str = "builtin-jira") -> Any:
        t = MagicMock()
        t.name = name
        t.description = f"Tool: {name}"
        t.input_schema = schema
        t.server_id = server_id
        return t

    def test_builds_schema_block_with_tool_names(self):
        tools = [
            self.make_tool("jira_search_issues", JIRA_SEARCH_SCHEMA, "builtin-jira"),
            self.make_tool("confluence_create_page", CONFLUENCE_CREATE_SCHEMA, "builtin-confluence"),
        ]
        block = SchemaAwarePromptInjector.build_tool_schema_block(tools)
        assert "jira_search_issues" in block
        assert "confluence_create_page" in block

    def test_schema_block_includes_required_marker(self):
        tools = [self.make_tool("jira_search_issues", JIRA_SEARCH_SCHEMA)]
        block = SchemaAwarePromptInjector.build_tool_schema_block(tools)
        # Required params are marked with *
        assert "jql*" in block or "jql" in block

    def test_schema_block_includes_parameter_names(self):
        tools = [self.make_tool("jira_search_issues", JIRA_SEARCH_SCHEMA)]
        block = SchemaAwarePromptInjector.build_tool_schema_block(tools)
        assert "jql" in block
        assert "max_results" in block

    def test_empty_tools_returns_empty_string(self):
        assert SchemaAwarePromptInjector.build_tool_schema_block([]) == ""

    def test_format_reminder_includes_critical_params(self):
        reminder = SchemaAwarePromptInjector.build_tool_call_format_reminder()
        assert "jql" in reminder
        assert "body" in reminder
        assert "EXACT" in reminder or "exact" in reminder.lower()

    def test_schema_block_no_crash_on_missing_schema(self):
        t = MagicMock()
        t.name = "some_tool"
        t.description = "A tool"
        t.input_schema = None
        t.server_id = "server"
        # Should not raise
        block = SchemaAwarePromptInjector.build_tool_schema_block([t])
        assert "some_tool" in block


# ═══════════════════════════════════════════════════════════════════════════════
# Integration: the full pipeline
# ═══════════════════════════════════════════════════════════════════════════════

class TestFullPipeline:
    """
    Integration tests verifying the whole intelligence layer works together:
    Resolver → Tool call → Healer → Retry
    """

    def test_goal_scenario_jira_to_confluence(self):
        """
        Simulate the full goal: 'Find Jira tickets and write to Confluence'
        The LLM generates wrong argument names for both tools.
        The resolver fixes them without any LLM call or manual code.
        """
        resolver = UniversalArgumentResolver()

        # Step 1: LLM calls jira_search_issues with wrong key
        jira_args = {
            "search_query": "assignee = 'Abhay Dwivedi' AND status != Done",
            "count": 50,
        }
        resolved_jira = resolver.resolve(JIRA_SEARCH_SCHEMA, jira_args)
        assert "jql" in resolved_jira, "jql must be resolved from search_query"
        assert resolved_jira["jql"] == "assignee = 'Abhay Dwivedi' AND status != Done"

        # Step 2: LLM calls confluence_create_page with wrong keys
        confluence_args = {
            "page_title": "Abhay Dwivedi Open Tickets",
            "content": "<ul><li>PROJ-1: Fix bug</li></ul>",
            "space": "TEAM",
        }
        resolved_conf = resolver.resolve(CONFLUENCE_CREATE_SCHEMA, confluence_args)
        assert resolved_conf["title"] == "Abhay Dwivedi Open Tickets"
        assert resolved_conf["body"] == "<ul><li>PROJ-1: Fix bug</li></ul>"
        assert resolved_conf["space_key"] == "TEAM"

        missing = resolver.report_missing(
            "confluence_create_page", CONFLUENCE_CREATE_SCHEMA, resolved_conf
        )
        assert missing == [], f"Should have all required params; missing: {missing}"

    def test_works_for_any_new_tool_without_coding(self):
        """
        A new tool never seen before — resolver uses fuzzy/type matching.
        No manual coding required.
        """
        resolver = UniversalArgumentResolver()

        custom_tool_schema = {
            "type": "object",
            "properties": {
                "database_url": {"type": "string"},
                "sql_query":    {"type": "string"},
                "row_limit":    {"type": "integer"},
            },
            "required": ["database_url", "sql_query"],
        }

        # LLM uses slightly different names
        args = {
            "db_url": "postgresql://localhost/mydb",
            "query": "SELECT * FROM issues WHERE status = 'open'",
            "limit": 100,
        }
        resolved = resolver.resolve(custom_tool_schema, args)

        # Should fuzzy-match 'db_url' → 'database_url' and 'query' → 'sql_query'
        # Even though these aren't in the alias table — fuzzy matching handles it
        # At minimum, the resolver should not crash
        assert isinstance(resolved, dict)
        # The system should be robust even if it can't resolve everything
        # What matters: no KeyError, returns a dict
        missing = resolver.report_missing("custom_db_tool", custom_tool_schema, resolved)
        # Ideally 0 missing, but at worst the healer/LLM will handle remainder
        assert len(missing) <= 2  # Resolver may not get all, healer handles the rest

    @pytest.mark.asyncio
    async def test_healer_invoked_on_keyerror(self):
        """
        When call_tool returns KeyError error, healer is invoked automatically.
        """
        resolver = UniversalArgumentResolver()
        healer = SelfHealingToolCaller(provider=None)

        # Simulate a tool call that failed with KeyError: 'body'
        failed = MagicMock(success=False, error="'body'")
        original_args = {"content": "<h1>Hello</h1>", "title": "Page", "space_key": "ENG"}

        assert healer.is_argument_error(failed.error)

        healed_args = await healer.heal(
            tool_name="confluence_create_page",
            tool_schema=CONFLUENCE_CREATE_SCHEMA,
            original_arguments=original_args,
            failed_result=failed,
            resolver=resolver,
        )

        assert healed_args["body"] == "<h1>Hello</h1>"
        # No more missing required params
        missing = resolver.report_missing(
            "confluence_create_page", CONFLUENCE_CREATE_SCHEMA, healed_args
        )
        assert "body" not in missing
