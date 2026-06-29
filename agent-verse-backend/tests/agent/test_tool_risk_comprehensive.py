"""Comprehensive tests for app/agent/tool_risk.py — targets 90%+ statement coverage."""
from __future__ import annotations

import pytest

from app.agent.tool_risk import _name_tokens, classify_tool_risk


# ── _name_tokens ──────────────────────────────────────────────────────────────

def test_name_tokens_snake_case() -> None:
    tokens = _name_tokens("delete_issue")
    assert "delete" in tokens
    assert "issue" in tokens


def test_name_tokens_camel_case() -> None:
    tokens = _name_tokens("createIssue")
    assert "create" in tokens
    assert "issue" in tokens


def test_name_tokens_pascal_case() -> None:
    tokens = _name_tokens("CreateSummaryPage")
    assert "create" in tokens
    assert "summary" in tokens
    assert "page" in tokens


def test_name_tokens_mixed() -> None:
    tokens = _name_tokens("fetch_open_issues")
    assert "fetch" in tokens
    assert "open" in tokens
    assert "issues" in tokens


def test_name_tokens_empty() -> None:
    tokens = _name_tokens("")
    assert tokens == frozenset()


def test_name_tokens_all_caps() -> None:
    tokens = _name_tokens("API_KEY")
    assert "api" in tokens
    assert "key" in tokens


# ── classify_tool_risk — Jira/Atlassian context ───────────────────────────────

def test_jira_read_tool() -> None:
    assert classify_tool_risk("get_issue", "jira") == "read"


def test_jira_list_tool() -> None:
    assert classify_tool_risk("list_issues", "jira") == "read"


def test_jira_search_tool() -> None:
    assert classify_tool_risk("search_issues", "jira") == "read"


def test_jira_comment_tool() -> None:
    assert classify_tool_risk("comment", "jira") == "write_low"


def test_jira_create_tool() -> None:
    assert classify_tool_risk("create_issue", "jira") == "write_high"


def test_jira_update_tool() -> None:
    assert classify_tool_risk("update_issue", "jira") == "write_high"


def test_jira_delete_tool() -> None:
    assert classify_tool_risk("delete_issue", "jira") == "destructive"


def test_jira_close_tool() -> None:
    assert classify_tool_risk("close_sprint", "jira") == "destructive"


def test_jira_unknown_tool_defaults_to_write_high() -> None:
    assert classify_tool_risk("unknown_jira_op", "jira") == "write_high"


def test_atlassian_context_recognized() -> None:
    # atlassian in server_name should trigger Jira path
    result = classify_tool_risk("get_issue", "atlassian")
    assert result == "read"


# ── classify_tool_risk — high-risk connectors ─────────────────────────────────

def test_stripe_any_tool_is_write_high() -> None:
    assert classify_tool_risk("get_balance", "stripe") == "write_high"


def test_payment_connector_is_write_high() -> None:
    assert classify_tool_risk("list_transactions", "payment_gateway") == "write_high"


def test_billing_connector_is_write_high() -> None:
    assert classify_tool_risk("get_invoice", "billing") == "write_high"


def test_production_connector_is_write_high() -> None:
    assert classify_tool_risk("restart_service", "production") == "write_high"


def test_prod_in_tool_name_is_write_high() -> None:
    assert classify_tool_risk("deploy_to_prod", "") == "write_high"


def test_deploy_in_tool_name_is_write_high() -> None:
    assert classify_tool_risk("deploy", "") == "write_high"


# ── classify_tool_risk — generic connectors ───────────────────────────────────

def test_generic_delete_tool() -> None:
    assert classify_tool_risk("delete_record", "github") == "destructive"


def test_generic_destroy_tool() -> None:
    assert classify_tool_risk("destroy_container", "docker") == "destructive"


def test_generic_drop_tool() -> None:
    assert classify_tool_risk("drop_table", "database") == "destructive"


def test_generic_purge_tool() -> None:
    assert classify_tool_risk("purge_cache", "redis") == "destructive"


def test_generic_create_tool() -> None:
    assert classify_tool_risk("create_branch", "github") == "write_high"


def test_generic_merge_tool() -> None:
    assert classify_tool_risk("merge_pr", "github") == "write_high"


def test_generic_send_tool() -> None:
    assert classify_tool_risk("send_message", "slack") == "write_high"


def test_generic_update_tool() -> None:
    assert classify_tool_risk("update_status", "github") == "write_low"


def test_generic_comment_tool() -> None:
    assert classify_tool_risk("add_comment", "github") == "write_low"


def test_generic_get_tool() -> None:
    assert classify_tool_risk("get_repo", "github") == "read"


def test_generic_list_tool() -> None:
    assert classify_tool_risk("list_branches", "github") == "read"


def test_generic_search_tool() -> None:
    assert classify_tool_risk("search_code", "github") == "read"


def test_generic_analyze_tool() -> None:
    assert classify_tool_risk("analyze_logs", "datadog") == "read"


def test_unknown_tool_defaults_to_read() -> None:
    assert classify_tool_risk("completely_unknown_op", "unknown_server") == "read"


def test_no_server_name_uses_tool_name_only() -> None:
    result = classify_tool_risk("delete_user")
    assert result == "destructive"


def test_terminate_is_destructive() -> None:
    assert classify_tool_risk("terminate_instance", "aws") == "destructive"


def test_revoke_is_destructive() -> None:
    assert classify_tool_risk("revoke_token", "auth") == "destructive"


def test_truncate_is_destructive() -> None:
    assert classify_tool_risk("truncate_table", "postgres") == "destructive"


def test_charge_is_write_high() -> None:
    assert classify_tool_risk("charge_customer", "billing") == "write_high"


def test_refund_is_write_high() -> None:
    assert classify_tool_risk("refund_payment", "billing") == "write_high"


def test_approve_is_write_high() -> None:
    assert classify_tool_risk("approve_request", "workflow") == "write_high"


def test_tag_is_write_low() -> None:
    assert classify_tool_risk("tag_resource", "aws") == "write_low"


def test_set_is_write_low() -> None:
    assert classify_tool_risk("set_value", "config") == "write_low"


def test_combined_server_and_tool_name_logic() -> None:
    # server + tool combined in lower case is used for matching
    result = classify_tool_risk("run_op", "stripe_connector")
    assert result == "write_high"  # stripe in combined string
