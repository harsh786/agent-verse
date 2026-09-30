"""Tool risk classification for governed real tool calls.

Covers all major connectors: GitHub, Slack, Stripe, Confluence, Datadog,
Salesforce, Jira, and generic DB / infrastructure tools.

Jira/Atlassian tools use a preserved, token-based classifier for full
backward-compatibility with existing governance rules.  All other connectors
use a new substring-based classifier that covers the full breadth of MCP
tool names encountered in production.
"""

from __future__ import annotations

import re
from typing import Literal

ToolRisk = Literal["read", "write_low", "write_high", "destructive", "unknown"]

# ── Jira/Atlassian-specific token sets (preserved) ───────────────────────────

_JIRA_CONTEXT_TOKENS = frozenset({"jira", "atlassian"})

_JIRA_READ_VERBS = frozenset(
    {
        "get",
        "list",
        "search",
        "find",
        "fetch",
        "query",
        "show",
        "browse",
        "describe",
        "count",
        "summary",
        "check",
        "view",
        "inspect",
    }
)

_JIRA_WRITE_LOW_VERBS = frozenset({"comment"})

_JIRA_WRITE_HIGH_VERBS = frozenset(
    {
        "assign",
        "create",
        "edit",
        "label",
        "labels",
        "merge",
        "resolve",
        "sprint",
        "status",
        "transition",
        "update",
    }
)

_JIRA_DESTRUCTIVE_VERBS = frozenset(
    {
        "bulk",
        "close",
        "closed",
        "delete",
        "destroy",
        "done",
        "remove",
        "terminate",
        "revoke",
        "purge",
        "wipe",
    }
)

# ── Comprehensive rules for all other connectors ──────────────────────────────

# Any tool on these connectors is at least write_high regardless of verb.
_HIGH_RISK_CONNECTORS = frozenset(
    [
        "stripe",
        "payment",
        "billing",
        "finance",
        "bank",
        "production",
        "prod",
        "deploy",
    ]
)

_DESTRUCTIVE_TOKENS = frozenset(
    [
        "delete",
        "destroy",
        "drop",
        "truncate",
        "purge",
        "wipe",
        "terminate",
        "revoke",
        "remove",
    ]
)

_WRITE_HIGH_TOKENS = frozenset(
    [
        "create",
        "create_issue",
        "create_pr",
        "merge",
        "deploy",
        "transition",
        "close",
        "resolve",
        "publish",
        "send",
        "charge",
        "refund",
        "transfer",
        "update_permission",
        "add_member",
        "approve",
        "reject",
        "override",
    ]
)

_WRITE_LOW_TOKENS = frozenset(
    [
        "update",
        "edit",
        "patch",
        "comment",
        "assign",
        "label",
        "tag",
        "set",
        "modify",
        "change",
        "rename",
    ]
)

_READ_TOKENS = frozenset(
    [
        "get",
        "list",
        "search",
        "fetch",
        "query",
        "find",
        "show",
        "describe",
        "status",
        "check",
        "read",
        "view",
        "browse",
        "count",
        "summary",
        "analyze",
        "inspect",
    ]
)


def _name_tokens(name: str) -> frozenset[str]:
    """Split a camelCase / snake_case / PascalCase name into lowercase tokens."""
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", separated)
    separated = re.sub(r"[^A-Za-z0-9]+", " ", separated)
    return frozenset(part.lower() for part in separated.split() if part)


def classify_tool_risk(tool_name: str, server_name: str = "") -> str:
    """Classify a tool into a risk tier.

    Parameters
    ----------
    tool_name:
        The name of the tool (e.g. ``"delete_issue"``).
    server_name:
        The MCP server / connector name (e.g. ``"jira"``).

    Returns
    -------
    str
        One of ``"read"``, ``"write_low"``, ``"write_high"``, or
        ``"destructive"``.  An unrecognised tool defaults to ``"write_high"`` so it
        requires human approval under supervised / bounded-autonomous modes (it
        used to default to ``"read"`` and silently skip approval).
    """
    if not tool_name.strip():
        return "read"  # no tool named → no action to gate

    # 0. Built-ins with a known, declared risk (checked before token heuristics).
    builtin = _builtin_risk(tool_name)
    if builtin is not None:
        return builtin

    combined = f"{server_name} {tool_name}".lower()

    # 1. High-risk connector override — Stripe, billing, etc. → always write_high
    for c in _HIGH_RISK_CONNECTORS:
        if c in combined:
            return "write_high"

    # 2. Jira/Atlassian context — use the preserved token-based classifier
    tool_tokens = _name_tokens(tool_name)
    server_tokens = _name_tokens(server_name)
    all_tokens = tool_tokens | server_tokens

    if all_tokens & _JIRA_CONTEXT_TOKENS:
        if tool_tokens & _JIRA_DESTRUCTIVE_VERBS:
            return "destructive"
        if tool_tokens & _JIRA_READ_VERBS:
            return "read"
        if tool_tokens & _JIRA_WRITE_HIGH_VERBS:
            return "write_high"
        if tool_tokens & _JIRA_WRITE_LOW_VERBS:
            return "write_low"
        # Unknown Jira tool — default to conservative write_high
        return "write_high"

    # 3. Generic connector classifier (GitHub, Slack, Datadog, Salesforce, etc.)
    for t in _DESTRUCTIVE_TOKENS:
        if t in combined:
            return "destructive"
    for t in _WRITE_HIGH_TOKENS:
        if t in combined:
            return "write_high"
    for t in _WRITE_LOW_TOKENS:
        if t in combined:
            return "write_low"
    for t in _READ_TOKENS:
        if t in combined:
            return "read"

    # Unknown tool: default to the approval-requiring class, never to "read".
    return _UNKNOWN_TOOL_RISK


_UNKNOWN_TOOL_RISK = "write_high"

# Platform built-ins whose names carry no read/write verb the heuristics recognise.
_BUILTIN_TOOL_RISK: dict[str, str] = {
    "web_search": "read",
    "parse_document": "read",
    "extract_document": "read",
    "save_artifact": "write_low",
    "knowledge.ingest": "write_low",
}

# RPA tools declare their own risk vocabulary (read / low / high). Read-only ones map
# to "read"; interactive navigation to "write_low".
_RPA_RISK_MAP = {"read": "read", "low": "write_low", "high": "write_low"}

# Declared-"high" RPA tools whose single call has an external or irreversible effect
# (submit data to a third party, upload a local file, write a download to disk).
# They used to be mapped to write_low with every other "high" tool, so they ran with
# no HITL gate at all. They are write_high: approval unless the operator opted in
# (connector auto_approve / fully-autonomous + ALLOW_FULLY_AUTONOMOUS_WRITE_HIGH).
# Pure interaction (click / type / select_option) stays write_low so browser
# navigation is not blocked on an approval per keystroke.
_RPA_WRITE_HIGH_TOOLS = frozenset({"rpa_submit_form", "rpa_upload_file", "rpa_download_file"})


def _builtin_risk(tool_name: str) -> str | None:
    name = tool_name.split(".")[-1] if tool_name.startswith("rpa.") else tool_name
    if name in _BUILTIN_TOOL_RISK:
        return _BUILTIN_TOOL_RISK[name]
    if name.startswith("rpa_"):
        try:
            from app.rpa.tools import classify_rpa_tool_risk

            declared = str(classify_rpa_tool_risk(name))
        except Exception:
            return None
        if declared == "high" and name in _RPA_WRITE_HIGH_TOOLS:
            return "write_high"
        if declared == "high":
            # RPA_INTERACTION_RISK (default write_low): operators can gate
            # click / type / select behind approval too.
            try:
                from app.core.config import get_settings

                return str(get_settings().rpa_interaction_risk)
            except Exception:
                return "write_high"  # unreadable config: the gated class
        return _RPA_RISK_MAP.get(declared)
    return None
