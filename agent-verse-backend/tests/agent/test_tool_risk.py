"""Tests for the comprehensive tool risk classifier."""
from __future__ import annotations

import pytest

from app.agent.tool_risk import classify_tool_risk


# ── destructive ───────────────────────────────────────────────────────────────

def test_jira_delete_issue_is_destructive() -> None:
    assert classify_tool_risk("delete_issue", "jira") == "destructive"


def test_db_drop_table_is_destructive() -> None:
    assert classify_tool_risk("drop_table", "db") == "destructive"


def test_generic_purge_is_destructive() -> None:
    assert classify_tool_risk("purge_records", "storage") == "destructive"


# ── write_high ────────────────────────────────────────────────────────────────

def test_github_merge_pr_is_write_high() -> None:
    assert classify_tool_risk("merge_pr", "github") == "write_high"


def test_slack_send_message_is_write_high() -> None:
    assert classify_tool_risk("send_message", "slack") == "write_high"


def test_stripe_create_charge_is_write_high() -> None:
    # stripe connector override → write_high regardless of verb
    assert classify_tool_risk("create_charge", "stripe") == "write_high"


def test_jira_create_issue_is_write_high() -> None:
    assert classify_tool_risk("create_issue", "jira") == "write_high"


def test_jira_transition_issue_is_write_high() -> None:
    assert classify_tool_risk("transition_issue", "jira") == "write_high"


def test_confluence_publish_page_is_write_high() -> None:
    assert classify_tool_risk("publish_page", "confluence") == "write_high"


# ── write_low ─────────────────────────────────────────────────────────────────

def test_github_update_file_is_write_low() -> None:
    assert classify_tool_risk("update_file", "github") == "write_low"


def test_jira_comment_is_write_low() -> None:
    assert classify_tool_risk("add_comment", "jira") == "write_low"


# ── read ──────────────────────────────────────────────────────────────────────

def test_jira_search_issues_is_read() -> None:
    assert classify_tool_risk("search_issues", "jira") == "read"


def test_confluence_get_page_is_read() -> None:
    assert classify_tool_risk("get_page", "confluence") == "read"


def test_github_list_repos_is_read() -> None:
    assert classify_tool_risk("list_repos", "github") == "read"


def test_datadog_get_metrics_is_read() -> None:
    assert classify_tool_risk("get_metrics", "datadog") == "read"


def test_salesforce_query_is_read() -> None:
    assert classify_tool_risk("query_records", "salesforce") == "read"


# ── default / unknown ─────────────────────────────────────────────────────────

def test_unknown_tool_defaults_to_read() -> None:
    """Unrecognised tools must default to 'read' (safe)."""
    assert classify_tool_risk("frobnicate_widget", "unknown_connector") == "read"


def test_empty_names_default_to_read() -> None:
    assert classify_tool_risk("", "") == "read"
