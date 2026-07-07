"""ToolTrace — per-goal observability trace for tool calls."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCallRecord:
    tool_name: str
    success: bool
    latency_ms: float
    error: str = ""
    args_summary: str = ""


class ToolTrace:
    def __init__(self, goal_id: str) -> None:
        self.goal_id = goal_id
        self.calls: list[ToolCallRecord] = []

    def record(
        self,
        tool_name: str,
        *,
        success: bool,
        latency_ms: float,
        error: str = "",
        args_summary: str = "",
    ) -> None:
        self.calls.append(
            ToolCallRecord(
                tool_name=tool_name,
                success=success,
                latency_ms=latency_ms,
                error=error,
                args_summary=args_summary,
            )
        )

    @property
    def success_rate(self) -> float:
        if not self.calls:
            return 1.0
        return sum(1 for c in self.calls if c.success) / len(self.calls)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "call_count": len(self.calls),
            "success_rate": self.success_rate,
            "calls": [
                {
                    "tool": c.tool_name,
                    "success": c.success,
                    "latency_ms": c.latency_ms,
                }
                for c in self.calls
            ],
        }
