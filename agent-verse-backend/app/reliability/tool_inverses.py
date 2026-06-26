"""Tool inverse registry — maps tool names to their undo functions.

When an agent step calls a tool, the inverse function is registered
with the RollbackEngine so that step can be undone on failure.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Registry: tool_name -> async callable that undoes the tool call
_INVERSE_REGISTRY: dict[str, Callable[[dict[str, Any]], Any]] = {}


def register_inverse(
    tool_name: str,
    fn: Callable[[dict[str, Any]], Any],
) -> None:
    """Register an inverse (undo) function for a tool."""
    _INVERSE_REGISTRY[tool_name] = fn


def get_inverse_fn(
    tool_name: str,
    arguments: dict[str, Any],
) -> Callable[[], Any]:
    """Return a zero-arg callable that undoes the named tool call.

    Falls back to a no-op lambda when no inverse is registered.
    """
    fn = _INVERSE_REGISTRY.get(tool_name)
    if fn is None:
        return lambda: None
    captured_args = dict(arguments)
    return lambda: fn(captured_args)


# ── Built-in inverses for Jira / common tools ─────────────────────────────────

def _inverse_jira_create_issue(args: dict[str, Any]) -> None:
    """Log the intent to delete a created Jira issue."""
    issue_id = args.get("issue_id") or args.get("id")
    if issue_id:
        logger.info("rollback_jira_create_issue issue_id=%s", issue_id)
        # In production: call jira.delete_issue(issue_id) via MCP client


def _inverse_confluence_create_page(args: dict[str, Any]) -> None:
    page_id = args.get("page_id") or args.get("id")
    if page_id:
        logger.info("rollback_confluence_create_page page_id=%s", page_id)


def _inverse_slack_send_message(args: dict[str, Any]) -> None:
    ts = args.get("ts") or args.get("message_ts")
    if ts:
        logger.info("rollback_slack_send_message message_ts=%s", ts)


# Register built-in inverses
register_inverse("jira:create_issue", _inverse_jira_create_issue)
register_inverse("jira_create_issue", _inverse_jira_create_issue)
register_inverse("confluence:create_page", _inverse_confluence_create_page)
register_inverse("confluence_create_page", _inverse_confluence_create_page)
register_inverse("slack:send_message", _inverse_slack_send_message)
register_inverse("slack_send_message", _inverse_slack_send_message)
