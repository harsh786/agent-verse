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
            return matches[0] if len(matches) == 1 else None

        server_name, tool_name = name.split(".", 1)
        server_name_lower = server_name.lower()
        return next(
            (
                tool
                for tool in self.tools
                if tool.name == tool_name
                and server_name_lower in {tool.server_name.lower(), tool.server_id.lower()}
            ),
            None,
        )
