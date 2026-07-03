"""Structured tool-call parsing for executor output."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolCall:
    tool: str
    arguments: dict[str, Any]


_JIRA_SEARCH_TOOL_ALIASES = {
    "jirasearchissues",
    "jiraissuesearch",
    "jirasearch",
    "searchjira",
}


def _tool_name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _canonical_tool_name(name: str) -> str:
    if _tool_name_key(name) in _JIRA_SEARCH_TOOL_ALIASES:
        return "jira_search_issues"
    return name


def extract_tool_call(text: str) -> ToolCall | None:
    """Parse a structured tool call from JSON or a markdown JSON block."""
    candidate = text.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", candidate, flags=re.DOTALL)
    if match:
        candidate = match.group(1).strip()

    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    tool = obj.get("tool") or obj.get("tool_name")
    if not tool:
        return None
    tool_text = str(tool)
    if (
        tool_text in {"server_name.tool_name", "tool_name", "python.datetime"}
        or tool_text.startswith("server_name.")
    ):
        return None

    if "arguments" in obj:
        args = obj["arguments"]
    elif "args" in obj:
        args = obj["args"]
    else:
        args = {}
    if not isinstance(args, dict):
        return None
    return ToolCall(tool=tool_text, arguments=args)


def _looks_like_placeholder_jql(jql: str) -> bool:
    placeholders = {
        "project = test",
        "project = TEST",
        "project_name",
        "date_calculated",
        "date_calculated_in_step",
    }
    lower = jql.lower()
    return any(item.lower() in lower for item in placeholders)


def _named_assignee_from_text(text: str) -> str:
    match = re.search(
        r"assigned\s+(?:to|on)\s+([A-Z][A-Za-z]+(?:[ \t]+[A-Z][A-Za-z]+){0,3})",
        text,
    )
    if match is None:
        return ""
    assignee = match.group(1).strip()
    if assignee.lower() in {"me", "you"}:
        return ""
    return assignee


def _jql_from_goal_or_step(text: str) -> str:
    lower = text.lower()
    if "assigned to me" in lower or "assigned to you" in lower:
        return "assignee = currentUser() AND created >= -26w ORDER BY created DESC"
    named_assignee = _named_assignee_from_text(text)
    if named_assignee:
        # Jira Cloud requires account IDs in JQL, not display names.
        # Try to resolve the display name to an account ID via the Jira user API.
        # Fall back to displayName search which works on some Jira instances.
        account_id = _resolve_jira_account_id(named_assignee)
        if account_id:
            return f'assignee = "{account_id}" ORDER BY created DESC'
        # Fallback: displayName quoted search (works on Jira Server / some Cloud)
        return f'assignee = "{named_assignee}" ORDER BY created DESC'
    if "last 6 months" in lower:
        return "created >= -26w ORDER BY created DESC"
    return ""


def _resolve_jira_account_id(display_name: str) -> str:
    """Look up a Jira user account ID by display name.

    Calls GET /rest/api/3/user/search?query=<name> using the credentials
    configured via JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN env vars.
    Returns the accountId of the first exact (case-insensitive) display name
    match, or an empty string if not found or credentials are unavailable.
    """
    import base64
    import os
    import urllib.request

    base = os.getenv("JIRA_BASE_URL", "").rstrip("/")
    email = os.getenv("JIRA_EMAIL", "")
    token = os.getenv("JIRA_API_TOKEN", "")
    if not base or not email or not token:
        return ""

    creds = base64.b64encode(f"{email}:{token}".encode()).decode()
    url = (
        f"{base}/rest/api/3/user/search"
        f"?query={urllib.request.quote(display_name)}&maxResults=10"
    )
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Basic {creds}",
                "Accept": "application/json",
            },
        )
        import json as _json
        with urllib.request.urlopen(req, timeout=5) as resp:
            users = _json.loads(resp.read())
        if not isinstance(users, list):
            return ""
        lower_name = display_name.lower()
        for user in users:
            if user.get("displayName", "").lower() == lower_name:
                return str(user.get("accountId", ""))
        # Partial match fallback
        for user in users:
            if lower_name in user.get("displayName", "").lower():
                return str(user.get("accountId", ""))
        return ""
    except Exception:
        return ""


def _resolve_jira_display_names_in_jql(jql: str) -> str:
    """Replace display-name strings in JQL assignee clauses with account IDs.

    Jira Cloud does not support assignee = "Display Name" — it requires the
    accountId.  This function detects patterns like:
        assignee = "Abhay Dwivedi"
        assignee in ("Abhay Dwivedi", "Jane Doe")
    and replaces each quoted value with the resolved accountId (when available).
    Values that already look like account IDs (contain ':') are left untouched.
    """
    def _replace_one(match: re.Match) -> str:
        name = match.group(1)
        # Already an account ID (e.g. "712020:abc...")
        if ":" in name:
            return match.group(0)
        aid = _resolve_jira_account_id(name)
        if aid:
            return match.group(0).replace(f'"{name}"', f'"{aid}"')
        return match.group(0)

    # Match quoted values after assignee = or inside assignee in (...)
    return re.sub(r'"([^"]{3,60})"', _replace_one, jql)


def repair_tool_call_arguments(call: ToolCall, step: str, goal: str = "") -> ToolCall:
    """Fill obvious missing arguments from the planner step text."""
    canonical_tool = _canonical_tool_name(call.tool)
    if canonical_tool != call.tool:
        call = ToolCall(tool=canonical_tool, arguments=call.arguments)
    if "jira_search_issues" not in call.tool:
        return call
    existing_jql = str(call.arguments.get("jql", ""))
    repaired_jql = ""
    if not existing_jql or _looks_like_placeholder_jql(existing_jql):
        repaired_jql = _jql_from_goal_or_step(f"{goal}\n{step}")
    if repaired_jql:
        return ToolCall(tool=call.tool, arguments={**call.arguments, "jql": repaired_jql})
    if existing_jql:
        # Even if JQL looks valid, resolve any display names → account IDs
        resolved_jql = _resolve_jira_display_names_in_jql(existing_jql)
        if resolved_jql != existing_jql:
            return ToolCall(tool=call.tool, arguments={**call.arguments, "jql": resolved_jql})
        return call
    match = re.search(r"JQL\s+['\"]([^'\"]+)['\"]", step, flags=re.IGNORECASE)
    if match is None:
        return call
    return ToolCall(tool=call.tool, arguments={**call.arguments, "jql": match.group(1)})
