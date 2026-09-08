"""SAFE-2: Verify that the tool allowlist is populated so hallucinated tool names
are rejected by the guardrail checker.

The bug: GuardrailChecker is constructed with an empty known_tools set, and
register_tools() is never called.  This means the registry allowlist check on
line 206-210 of guardrails.py is effectively disabled — *any* tool name passes.

These tests verify:
  1. Empty known_tools lets hallucinated tools through (pre-fix baseline).
  2. After populating the allowlist, hallucinated tools are rejected.
  3. Real discovered tools still pass after registration.
  4. The _populate_guardrail_allowlist helper extracts names from ToolContext.
"""
from __future__ import annotations

from app.agent.tool_context import ToolContext, ToolRef
from app.intelligence.guardrails import GuardrailChecker

# ── Unit tests: GuardrailChecker allowlist behaviour ─────────────────────────


class TestGuardrailToolAllowlist:
    """Validate that tool-name validation works when the allowlist is populated."""

    def test_empty_known_tools_allows_hallucinated_tool(self) -> None:
        """Pre-fix baseline: empty known_tools disables the registry check."""
        checker = GuardrailChecker()
        issues = checker.check(
            tool_name="completely_hallucinated_tool",
            tool_args={},
        )
        # With empty known_tools, the check is skipped — no issues
        assert issues == []

    def test_populated_allowlist_rejects_hallucinated_tool(self) -> None:
        """After registering real tools, a hallucinated name MUST be rejected."""
        checker = GuardrailChecker()
        checker.register_tools({"slack.post_message", "github.list_repos"})

        issues = checker.check(
            tool_name="totally_fake_tool_xyz",
            tool_args={},
        )
        assert len(issues) > 0
        assert any("Unknown tool" in issue for issue in issues)

    def test_registered_tool_passes_validation(self) -> None:
        """A legitimately discovered tool must pass after registration."""
        checker = GuardrailChecker()
        checker.register_tools({"jira.create_ticket", "slack.post_message"})

        issues = checker.check(tool_name="jira.create_ticket", tool_args={})
        assert issues == []

    def test_llm_call_always_allowed(self) -> None:
        """The 'llm_call' pseudo-tool is always whitelisted."""
        checker = GuardrailChecker()
        checker.register_tools({"github.list_repos"})

        issues = checker.check(tool_name="llm_call", tool_args={})
        assert issues == []


# ── Integration: _populate_guardrail_allowlist helper ────────────────────────


class TestPopulateGuardrailAllowlist:
    """Verify the helper that extracts tool names from ToolContext and
    registers them on the GuardrailChecker."""

    def test_populate_from_tool_context(self) -> None:
        """The helper should extract tool names and register them."""
        from app.services.goal_service import _populate_guardrail_allowlist

        checker = GuardrailChecker()
        tool_context = ToolContext(
            connectors=[],
            tools=[
                ToolRef(
                    server_id="s1",
                    server_name="slack",
                    name="post_message",
                    description="Post a message",
                    input_schema={},
                ),
                ToolRef(
                    server_id="s2",
                    server_name="github",
                    name="list_repos",
                    description="List repositories",
                    input_schema={},
                ),
            ],
        )

        _populate_guardrail_allowlist(checker, tool_context)

        # Real tools pass
        assert checker.check(tool_name="post_message", tool_args={}) == []
        assert checker.check(tool_name="list_repos", tool_args={}) == []
        # Hallucinated tool is rejected
        issues = checker.check(tool_name="fake_tool", tool_args={})
        assert len(issues) > 0
        assert any("Unknown tool" in i for i in issues)

    def test_populate_with_none_tool_context_is_noop(self) -> None:
        """When tool_context is None, the allowlist stays empty (no crash)."""
        from app.services.goal_service import _populate_guardrail_allowlist

        checker = GuardrailChecker()
        _populate_guardrail_allowlist(checker, None)

        # Still empty — registry check disabled, so no rejection
        issues = checker.check(tool_name="anything", tool_args={})
        assert issues == []

    def test_populate_with_empty_tools_is_noop(self) -> None:
        """When ToolContext has no tools, allowlist stays empty."""
        from app.services.goal_service import _populate_guardrail_allowlist

        checker = GuardrailChecker()
        tool_context = ToolContext(connectors=[], tools=[])

        _populate_guardrail_allowlist(checker, tool_context)

        # Empty — registry check disabled
        issues = checker.check(tool_name="anything", tool_args={})
        assert issues == []

    def test_populate_includes_rpa_tools(self) -> None:
        """RPA tools should also be in the allowlist."""
        from app.services.goal_service import _populate_guardrail_allowlist

        checker = GuardrailChecker()
        tool_context = ToolContext(
            connectors=[],
            tools=[
                ToolRef(
                    server_id="rpa",
                    server_name="rpa",
                    name="browser_navigate",
                    description="Navigate browser",
                    input_schema={},
                ),
                ToolRef(
                    server_id="mcp1",
                    server_name="jira",
                    name="create_ticket",
                    description="Create a Jira ticket",
                    input_schema={},
                ),
            ],
        )

        _populate_guardrail_allowlist(checker, tool_context)

        # Both RPA and MCP tools pass
        assert checker.check(tool_name="browser_navigate", tool_args={}) == []
        assert checker.check(tool_name="create_ticket", tool_args={}) == []
        # Hallucinated still blocked
        issues = checker.check(tool_name="nope", tool_args={})
        assert any("Unknown tool" in i for i in issues)
