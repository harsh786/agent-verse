"""Output guardrails — validate LLM-generated tool calls before execution.

Checks performed:
  1. Tool name must be in the known_tools registry (prevents hallucinated tools).
  2. (Future) Parameter schema validation against MCP tool spec.
  3. (Future) Unsafe content pattern detection.

An empty known_tools set disables the registry check (allows all tools).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GuardrailResult:
    blocked: bool
    reason: str = ""


class GuardrailChecker:
    """Validates tool calls against guardrail rules.

    Args:
        known_tools: Set of allowed tool names. Empty set disables registry check.
    """

    def __init__(self, *, known_tools: set[str]) -> None:
        self._known_tools = known_tools

    def check_tool_call(self, *, tool_name: str) -> GuardrailResult:
        if self._known_tools and tool_name not in self._known_tools:
            return GuardrailResult(
                blocked=True,
                reason=f"Unknown or hallucinated tool: '{tool_name}'",
            )
        return GuardrailResult(blocked=False)
