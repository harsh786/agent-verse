"""Tests for app.tool_runtime.tool_policy: per-tool access policy evaluation.

Covers the denied/allowed/HITL branches of ToolPolicy.evaluate and the
ToolAccessPolicy dataclass defaults.
"""
from __future__ import annotations

from app.tool_runtime.tool_policy import ToolAccessPolicy, ToolPolicy


class TestToolAccessPolicyDefaults:
    def test_defaults(self):
        policy = ToolAccessPolicy(tool_name="search", allowed=True)
        assert policy.requires_hitl is False
        assert policy.max_calls_per_goal == 50
        assert policy.denied_reason == ""


class TestToolPolicyEvaluate:
    def test_no_restrictions_allows_tool(self):
        policy = ToolPolicy()
        result = policy.evaluate("search", "tenant-1")
        assert result.allowed is True
        assert result.requires_hitl is False
        assert result.denied_reason == ""
        assert result.tool_name == "search"

    def test_denied_tool_is_blocked_with_reason(self):
        policy = ToolPolicy(denied_tools=["delete_db"])
        result = policy.evaluate("delete_db", "tenant-1")
        assert result.allowed is False
        assert result.denied_reason == "tool denied by policy"
        assert result.requires_hitl is False

    def test_hitl_tool_is_allowed_but_flagged(self):
        policy = ToolPolicy(hitl_tools=["deploy"])
        result = policy.evaluate("deploy", "tenant-1")
        assert result.allowed is True
        assert result.requires_hitl is True
        assert result.denied_reason == ""

    def test_denial_takes_precedence_over_hitl(self):
        # A tool listed in both denied and hitl sets must be denied — access
        # control (denied) is a stronger guarantee than a human-review gate.
        policy = ToolPolicy(denied_tools=["nuke"], hitl_tools=["nuke"])
        result = policy.evaluate("nuke", "tenant-1")
        assert result.allowed is False
        assert result.requires_hitl is False
        assert result.denied_reason == "tool denied by policy"

    def test_unrelated_tool_unaffected_by_other_restrictions(self):
        policy = ToolPolicy(denied_tools=["delete_db"], hitl_tools=["deploy"])
        result = policy.evaluate("read_file", "tenant-1")
        assert result.allowed is True
        assert result.requires_hitl is False

    def test_evaluate_is_tenant_agnostic_by_design(self):
        # tenant_id is accepted (for future per-tenant policy) but current
        # behavior does not vary the decision by tenant.
        policy = ToolPolicy(denied_tools=["delete_db"])
        result_a = policy.evaluate("delete_db", "tenant-a")
        result_b = policy.evaluate("delete_db", "tenant-b")
        assert result_a.allowed == result_b.allowed == False  # noqa: E712

    def test_none_lists_default_to_empty_sets(self):
        policy = ToolPolicy(denied_tools=None, hitl_tools=None)
        result = policy.evaluate("anything", "tenant-1")
        assert result.allowed is True
        assert result.requires_hitl is False
