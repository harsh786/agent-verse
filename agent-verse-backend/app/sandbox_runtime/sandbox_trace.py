from __future__ import annotations

from typing import Any


class SandboxTrace:
    def __init__(self, goal_id: str) -> None:
        self.goal_id = goal_id
        self.executions: list[dict[str, Any]] = []

    def record(
        self,
        sandbox_type: str,
        command: str,
        *,
        success: bool,
        latency_ms: float,
    ) -> None:
        self.executions.append(
            {
                "sandbox_type": sandbox_type,
                "command": command[:200],
                "success": success,
                "latency_ms": latency_ms,
            }
        )
