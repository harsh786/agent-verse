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
    placeholders = {"project = test", "project_name", "date_calculated", "date_calculated_in_step"}
    lower = jql.lower()
    return any(item in lower for item in placeholders)


def _jql_from_goal_or_step(text: str) -> str:
    lower = text.lower()
    if "assigned to me" in lower or "assigned to you" in lower:
        return "assignee = currentUser() AND created >= -26w ORDER BY created DESC"
    if "last 6 months" in lower:
        return "created >= -26w ORDER BY created DESC"
    return ""


def repair_tool_call_arguments(call: ToolCall, step: str, goal: str = "") -> ToolCall:
    """Fill obvious missing arguments from the planner step text."""
    if "jira_search_issues" not in call.tool:
        return call
    existing_jql = str(call.arguments.get("jql", ""))
    repaired_jql = ""
    if not existing_jql or _looks_like_placeholder_jql(existing_jql):
        repaired_jql = _jql_from_goal_or_step(f"{goal}\n{step}")
    if repaired_jql:
        return ToolCall(tool=call.tool, arguments={**call.arguments, "jql": repaired_jql})
    if existing_jql:
        return call
    match = re.search(r"JQL\s+['\"]([^'\"]+)['\"]", step, flags=re.IGNORECASE)
    if match is None:
        return call
    return ToolCall(tool=call.tool, arguments={**call.arguments, "jql": match.group(1)})
