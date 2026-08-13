"""Structured tool-call parsing for executor output."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Jira account-ID in-memory cache
# ---------------------------------------------------------------------------
_jira_account_cache: dict[str, tuple[str, float]] = {}  # display_name → (account_id, expires_at)
_JIRA_CACHE_TTL = 3600  # 1 hour


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


async def _jql_from_goal_or_step(text: str) -> str:
    lower = text.lower()
    if "assigned to me" in lower or "assigned to you" in lower:
        return "assignee = currentUser() AND created >= -26w ORDER BY created DESC"
    named_assignee = _named_assignee_from_text(text)
    if named_assignee:
        # Keep synthesized arguments deterministic. Connector execution may
        # resolve this display name with tenant-scoped Jira credentials, but
        # argument repair must not depend on a developer machine's environment.
        return f'assignee = "{named_assignee}" ORDER BY created DESC'
    if "last 6 months" in lower:
        return "created >= -26w ORDER BY created DESC"
    return ""


async def _resolve_jira_account_id(display_name: str) -> str:
    """Look up a Jira user account ID by display name (async, non-blocking).

    Calls GET /rest/api/3/user/search?query=<name> using the credentials
    configured via JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN env vars.
    Returns the accountId of the first exact (case-insensitive) display name
    match, or an empty string if not found or credentials are unavailable.

    Results are cached in ``_jira_account_cache`` for ``_JIRA_CACHE_TTL`` seconds
    to avoid repeated round-trips for the same display name within a goal run.
    """
    import base64
    import os

    # --- cache lookup ---
    now = time.monotonic()
    cached = _jira_account_cache.get(display_name)
    if cached is not None:
        account_id, expires_at = cached
        if now < expires_at:
            return account_id
        # expired — remove stale entry
        _jira_account_cache.pop(display_name, None)

    base = os.getenv("JIRA_BASE_URL", "").rstrip("/")
    email = os.getenv("JIRA_EMAIL", "")
    token = os.getenv("JIRA_API_TOKEN", "")
    if not base or not email or not token:
        return ""

    creds = base64.b64encode(f"{email}:{token}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Accept": "application/json"}

    try:
        import httpx

        async with httpx.AsyncClient(headers=headers, timeout=8.0) as client:
            resp = await client.get(
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
    result = ""
    for user in users:
        if user.get("displayName", "").lower() == lower_name:
            result = str(user.get("accountId", ""))
            break
    if not result:
        for user in users:
            if lower_name in user.get("displayName", "").lower():
                result = str(user.get("accountId", ""))
                break

    # --- populate cache (cache empty result too to suppress repeated 404s) ---
    _jira_account_cache[display_name] = (result, now + _JIRA_CACHE_TTL)
    return result


async def _resolve_jira_display_names_in_jql(jql: str) -> str:
    """Replace display-name strings in JQL assignee clauses with account IDs (async).

    Handles both single-quoted and double-quoted names:
        assignee = "Abhay Dwivedi"   → assignee = "712020:..."
        assignee = 'Abhay Dwivedi'   → assignee = "712020:..."
    Values that already look like account IDs (contain ':') are untouched.
    """
    # First, normalise single-quoted values to double-quoted in JQL
    # so the subsequent regex only needs to handle double quotes
    jql = re.sub(r"assignee\s*=\s*'([^']+)'", r'assignee = "\1"', jql)
    jql = re.sub(r"assignee\s+in\s*\(\s*'([^']+)'", r'assignee in ("\1"', jql)

    # Find all double-quoted strings that look like display names (3-80 chars, no colon).
    # Process in reverse order to preserve string offsets during replacement.
    pattern = re.compile(r'"([^"]{3,80})"')
    matches = list(pattern.finditer(jql))
    for m in reversed(matches):
        name = m.group(1)
        if ":" in name:  # already an account ID
            continue
        aid = await _resolve_jira_account_id(name)
        if aid:
            jql = jql[: m.start()] + f'"{aid}"' + jql[m.end() :]

    return jql


async def repair_tool_call_arguments(call: ToolCall, step: str, goal: str = "") -> ToolCall:
    """Fill obvious missing arguments from the planner step text."""
    canonical_tool = _canonical_tool_name(call.tool)
    if canonical_tool != call.tool:
        call = ToolCall(tool=canonical_tool, arguments=call.arguments)
    if "jira_search_issues" not in call.tool:
        return call
    existing_jql = str(call.arguments.get("jql", ""))
    repaired_jql = ""
    if not existing_jql or _looks_like_placeholder_jql(existing_jql):
        repaired_jql = await _jql_from_goal_or_step(f"{goal}\n{step}")
    if repaired_jql:
        return ToolCall(tool=call.tool, arguments={**call.arguments, "jql": repaired_jql})
    if existing_jql:
        # Even if JQL looks valid, resolve any display names → account IDs
        resolved_jql = await _resolve_jira_display_names_in_jql(existing_jql)
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

    # Alias match: if tool_name is the canonical form of an allowed tool
    # (e.g. "jira_search_issues" is the canonical alias of "jira_search")
    if any(_canonical_tool_name(t) == tool_name for t in allowed_tools):
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
