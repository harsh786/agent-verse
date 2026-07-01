"""Planner-facing tool context built from an agent's connectors."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolRef:
    server_id: str
    server_name: str
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolContext:
    connectors: list[dict[str, Any]]
    tools: list[ToolRef]

    def to_prompt_block(self) -> str:
        """Return a readable tool list suitable for planner prompt context."""
        if not self.tools:
            return "No connector tools available."

        lines = ["Available tools:"]
        for tool in self.tools:
            schema = json.dumps(tool.input_schema, sort_keys=True)
            lines.append(f"- {tool.server_name}.{tool.name}: {tool.description}")
            lines.append(f"  input_schema: {schema}")
        return "\n".join(lines)

    def find_tool(self, name: str) -> ToolRef | None:
        """Find a tool by unqualified name or by Server.tool_name."""
        if "." not in name:
            matches = [tool for tool in self.tools if tool.name == name]
            if matches:
                return matches[0]  # Return first registered (most recently discovered)
            return None

        def _normalize(value: str) -> str:
            return value.lower().replace(".", "").replace(" ", "")

        server_name, tool_name = name.rsplit(".", 1)
        server_name_key = _normalize(server_name)
        return next(
            (
                tool
                for tool in self.tools
                if tool.name == tool_name
                and (
                    server_name_key in {_normalize(tool.server_name), _normalize(tool.server_id)}
                    or (server_name_key == "jira" and "jira" in _normalize(tool.server_name))
                )
            ),
            None,
        )
