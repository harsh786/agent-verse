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

    if "arguments" in obj:
        args = obj["arguments"]
    elif "args" in obj:
        args = obj["args"]
    else:
        args = {}
    if not isinstance(args, dict):
        return None
    return ToolCall(tool=str(tool), arguments=args)
