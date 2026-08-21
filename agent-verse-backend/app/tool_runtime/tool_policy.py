"""ToolPolicy — per-tool access policy based on tenant, risk, and capability."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolAccessPolicy:
    tool_name: str
    allowed: bool
    requires_hitl: bool = False
    max_calls_per_goal: int = 50
    denied_reason: str = ""


class ToolPolicy:
    def __init__(
        self,
        denied_tools: list[str] | None = None,
        hitl_tools: list[str] | None = None,
    ) -> None:
        self._denied = set(denied_tools or [])
        self._hitl = set(hitl_tools or [])

    def evaluate(self, tool_name: str, tenant_id: str) -> ToolAccessPolicy:
        if tool_name in self._denied:
            return ToolAccessPolicy(
                tool_name=tool_name,
                allowed=False,
                denied_reason="tool denied by policy",
            )
        requires_hitl = tool_name in self._hitl
        return ToolAccessPolicy(
            tool_name=tool_name,
            allowed=True,
            requires_hitl=requires_hitl,
        )
