"""ToolPromptBuilder — builds per-step tool-use prompt from available tools."""

from __future__ import annotations

from typing import Any


class ToolPromptBuilder:
    def build(self, tools: list[dict[str, Any]], step_context: str = "") -> str:
        if not tools:
            return f"Step: {step_context}\n\nNo tools available — use parametric knowledge."
        tool_lines = "\n".join(f"  - {t['name']}: {t.get('description', '')}" for t in tools)
        return (
            f"Step: {step_context}\n\n"
            f"Available tools:\n{tool_lines}\n\n"
            f"Use only tools listed above. Return tool call as JSON."
        )
