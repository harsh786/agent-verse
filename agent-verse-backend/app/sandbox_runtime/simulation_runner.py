from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SimulationResult:
    simulated: bool
    tool_name: str
    tool_args: dict[str, Any]
    would_affect: str
    is_safe: bool = True


class SimulationRunner:
    def dry_run(self, tool_name: str, tool_args: dict[str, Any]) -> SimulationResult:
        affect = f"Would call {tool_name} with {list(tool_args.keys())}"
        return SimulationResult(
            simulated=True,
            tool_name=tool_name,
            tool_args=tool_args,
            would_affect=affect,
            is_safe=True,
        )
