"""Planner-facing tool context built from an agent's connectors."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent.tool_selector import ToolSelection


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
    tool_prompt_override: str | None = field(default=None)

    def to_prompt_block(self) -> str:
        """Return a readable tool list suitable for planner prompt context.

        When ``tool_prompt_override`` is set (e.g. pre-rendered by ToolSelector
        with tiered tiers), it is returned verbatim so callers always get a
        single, consistent string.
        """
        if self.tool_prompt_override is not None:
            return self.tool_prompt_override

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
        jira_search_aliases = {
            "jirasearchissues",
            "jiraissuesearch",
            "jirasearch",
            "searchjira",
        }

        def _normalize(value: str) -> str:
            return value.lower().replace(".", "").replace(" ", "").replace("_", "")

        name_key = _normalize(name)
        alias_matches = [
            tool
            for tool in self.tools
            if name_key == _normalize(tool.name)
            or (
                # Both the lookup name AND the registered tool name are Jira-search aliases —
                # e.g. looking up "jira_search_issues" while tool is registered as "jira_search"
                name_key in jira_search_aliases
                and _normalize(tool.name) in jira_search_aliases
            )
            or (name_key in jira_search_aliases and tool.name == "jira_search_issues")
        ]
        if alias_matches:
            return alias_matches[0]

        if "." not in name:
            matches = [tool for tool in self.tools if tool.name == name]
            if matches:
                return matches[0]  # Return first registered (most recently discovered)
            return None

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
                    # Confluence aliases: confluence, confluenceapi, confluencecloud, etc.
                    or (
                        "confluence" in _normalize(tool.server_name)
                        and "confluence" in server_name_key
                    )
                )
            ),
            None,
        )


# ---------------------------------------------------------------------------
# Tiered rendering — used by ToolSelector output
# ---------------------------------------------------------------------------

def _render_signature(tool: Any) -> str:
    """One-line ``name(param1, param2) — description[:80]`` for a tool."""
    schema: dict[str, Any] = getattr(tool, "input_schema", {}) or {}
    props: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])
    req_params = [p for p in props if p in required]
    opt_params = [p for p in props if p not in required]
    all_params = req_params + [f"{p}?" for p in opt_params]
    param_str = ", ".join(all_params)
    desc = (getattr(tool, "description", "") or "")[:80]
    name = getattr(tool, "name", "")
    return f"- {name}({param_str}) — {desc}"


def to_tiered_prompt(selection: ToolSelection) -> str:  # type: ignore[type-arg]
    """Render a ToolSelection into a tiered prompt string.

    Three sections:
      - ``Available tools (full schema)``: full JSON schema for *selected* tier
      - ``More tools (signatures)``:       one-line signatures for *signature* tier
      - ``Other tools (names)``:           name + short desc for *names_only* tier
    """
    parts: list[str] = []

    # ── Tier 1: full schema ────────────────────────────────────────────────
    if selection.selected:
        lines = ["Available tools (full schema):"]
        for tool in selection.selected:
            schema = json.dumps(getattr(tool, "input_schema", {}), sort_keys=True)
            server_name = getattr(tool, "server_name", "")
            name = getattr(tool, "name", "")
            desc = getattr(tool, "description", "")
            lines.append(f"- {server_name}.{name}: {desc}")
            lines.append(f"  input_schema: {schema}")
        parts.append("\n".join(lines))

    # ── Tier 2: signatures ─────────────────────────────────────────────────
    if selection.signature:
        lines = ["More tools (signatures):"]
        for tool in selection.signature:
            lines.append(_render_signature(tool))
        parts.append("\n".join(lines))

    # ── Tier 3: names only ─────────────────────────────────────────────────
    if selection.names_only:
        lines = ["Other tools (names):"]
        for tool in selection.names_only:
            name = getattr(tool, "name", "")
            desc = (getattr(tool, "description", "") or "")[:60]
            lines.append(f"- {name}: {desc}")
        parts.append("\n".join(lines))

    return "\n\n".join(parts) if parts else "No connector tools available."
