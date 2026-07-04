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
    """
    Parse a structured tool call from the LLM's response text.

    Handles:
    - Plain JSON: {"tool": "jira_search_issues", "arguments": {...}}
    - Markdown code blocks: ```json {...} ```
    - Trailing garbage after valid JSON
    - Common malformed JSON (single quotes, trailing commas)
    - Nested tool calls: {"tool": ..., "input": {...}} or {"function": {"name": ...}}
    """
    candidate = text.strip()

    # A bare JSON array is never a valid tool call
    if candidate.startswith("["):
        return None
    match = re.search(r"```(?:json|tool_call)?\s*(.*?)```", candidate, flags=re.DOTALL)
    if match:
        candidate = match.group(1).strip()

    # Try direct JSON parse
    obj = _try_parse_json(candidate)

    # If failed, try to extract JSON object from the text
    if obj is None:
        json_match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if json_match:
            obj = _try_parse_json(json_match.group())

    if not isinstance(obj, dict):
        return None

    # Resolve tool name from various formats LLMs use
    tool = (
        obj.get("tool")
        or obj.get("tool_name")
        or obj.get("function_name")
        or obj.get("name")
        # Anthropic function calling: {"function": {"name": ..., "arguments": ...}}
        or (obj.get("function") or {}).get("name")
    )
    if not tool:
        return None
    tool_text = str(tool)

    # Skip placeholder/template values
    if (
        tool_text in {"server_name.tool_name", "tool_name", "python.datetime"}
        or tool_text.startswith("server_name.")
    ):
        return None

    # Resolve arguments from various formats
    args = (
        obj.get("arguments")
        or obj.get("args")
        or obj.get("input")
        or obj.get("parameters")
        or obj.get("params")
        # Anthropic: {"function": {"name": ..., "arguments": {...}}}
        or (obj.get("function") or {}).get("arguments")
        or {}
    )
    if not isinstance(args, dict):
        # If args is a JSON string, try to parse it to a dict
        if isinstance(args, str):
            parsed = _try_parse_json(args)
            if isinstance(parsed, dict):
                args = parsed
            else:
                return None  # Unparseable string args
        else:
            return None  # List, number, etc. — can't map to named params

    return ToolCall(tool=tool_text, arguments=args)


def _try_parse_json(text: str) -> dict | None:
    """Attempt to parse JSON with several repair strategies."""
    if not text:
        return None

    # Strategy 1: Standard parse
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        pass

    # Strategy 2: Find the first { ... } balanced block
    try:
        start = text.index("{")
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    result = json.loads(candidate)
                    return result if isinstance(result, dict) else None
    except (ValueError, json.JSONDecodeError):
        pass

    # Strategy 3: Repair common LLM JSON issues (single quotes, trailing commas)
    try:
        repaired = text
        # Single quotes → double quotes (careful not to break apostrophes in values)
        repaired = re.sub(r"(?<![\\])'([^']*)'(?=\s*[:{,\]}])", r'"\1"', repaired)
        # Trailing commas before } or ]
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        result = json.loads(repaired)
        return result if isinstance(result, dict) else None
    except (json.JSONDecodeError, Exception):
        pass

    return None


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

    base = os.getenv("JIRA_BASE_URL", "").rstrip("/")
    email = os.getenv("JIRA_EMAIL", "")
    token = os.getenv("JIRA_API_TOKEN", "")
    if not base or not email or not token:
        return ""

    creds = base64.b64encode(f"{email}:{token}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Accept": "application/json"}

    try:
        import httpx

        with httpx.Client(headers=headers, timeout=8.0) as client:
            resp = client.get(
                f"{base}/rest/api/3/user/search",
                params={"query": display_name, "maxResults": 10},
            )
            resp.raise_for_status()
            users = resp.json()
    except Exception:
        return ""

    if not isinstance(users, list):
        return ""
    lower_name = display_name.lower()
    for user in users:
        if user.get("displayName", "").lower() == lower_name:
            return str(user.get("accountId", ""))
    for user in users:
        if lower_name in user.get("displayName", "").lower():
            return str(user.get("accountId", ""))
    return ""


def _resolve_jira_display_names_in_jql(jql: str) -> str:
    """Replace display-name strings in JQL assignee clauses with account IDs.

    Handles both single-quoted and double-quoted names:
        assignee = "Abhay Dwivedi"   → assignee = "712020:..."
        assignee = 'Abhay Dwivedi'   → assignee = "712020:..."
    Values that already look like account IDs (contain ':') are untouched.
    """
    # First, normalise single-quoted values to double-quoted in JQL
    # so the subsequent regex only needs to handle double quotes
    jql = re.sub(r"assignee\s*=\s*'([^']+)'", r'assignee = "\1"', jql)
    jql = re.sub(r"assignee\s+in\s*\(\s*'([^']+)'", r'assignee in ("\1"', jql)

    def _replace_one(m: re.Match) -> str:
        name = m.group(1)
        if ":" in name:  # already an account ID
            return m.group(0)
        aid = _resolve_jira_account_id(name)
        if aid:
            return f'"{aid}"'
        return m.group(0)

    # Match double-quoted strings that look like display names (3–80 chars, no colon)
    return re.sub(r'"([^"]{3,80})"', _replace_one, jql)


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


# ── Built-in tool prefixes that always bypass MCP validation ─────────────────
_ALWAYS_ALLOWED_PREFIXES: frozenset[str] = frozenset({
    "rpa_",
    "civilization_",
})


def validate_tool_name(tool_name: str, allowed_tools: set[str]) -> str | None:
    """Validate that *tool_name* is in the allowed set.

    Returns:
        None  — tool is valid, proceed with dispatch.
        str   — human-readable rejection message (use as raw_output, skip dispatch).

    Built-in prefixes (rpa_*, civilization_*) are always allowed.
    """
    if not tool_name:
        return "Tool name is empty — cannot dispatch."

    # Built-in tools are always allowed
    for prefix in _ALWAYS_ALLOWED_PREFIXES:
        if tool_name.startswith(prefix) or tool_name.split(".")[-1].startswith(
            prefix.rstrip("_")
        ):
            return None

    # If no allowed_tools were provided (discovery failed), be permissive
    if not allowed_tools:
        return None

    # Exact match
    if tool_name in allowed_tools:
        return None

    # Suffix match: allow "jira_search_issues" to match "jira_server.jira_search_issues"
    bare = tool_name.split(".")[-1]
    if any(bare == t.split(".")[-1] for t in allowed_tools):
        return None

    # Rejected
    available_sample = ", ".join(sorted(allowed_tools)[:8])
    if len(allowed_tools) > 8:
        available_sample += f" … (+{len(allowed_tools) - 8} more)"
    return (
        f"[TOOL NOT AVAILABLE] '{tool_name}' is not in the discovered tool list. "
        f"Available tools: {available_sample}. "
        "Choose one of the available tools or respond with INSUFFICIENT DATA."
    )


def validate_tool_arguments(
    arguments: dict | None,
    schema: dict | None,
) -> list[str]:
    """Validate *arguments* against a JSON Schema dict.

    Checks:
    - All ``required`` fields are present.
    - No extra fields beyond ``properties`` are present.

    Returns a list of human-readable error strings (empty = valid).
    Only validates ``type: object`` schemas; returns [] for any other shape.
    Intentionally lenient on type mismatches (leave those to the server).
    """
    if not schema or not arguments:
        return []

    if schema.get("type") != "object":
        return []

    errors: list[str] = []
    properties: dict = schema.get("properties") or {}
    required: list[str] = schema.get("required") or []

    # 1. Missing required fields
    for field in required:
        if field not in arguments:
            errors.append(
                f"Missing required argument '{field}'. "
                f"Expected type: {properties.get(field, {}).get('type', 'unknown')}."
            )

    # 2. Unknown fields (warn — some servers are lenient but LLM should know)
    if properties:
        for key in arguments:
            if key not in properties:
                errors.append(
                    f"Unexpected argument '{key}' is not in the tool schema. "
                    f"Valid fields: {list(properties.keys())}."
                )

    return errors
