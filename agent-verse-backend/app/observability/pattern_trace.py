from __future__ import annotations
from typing import Any


def emit_pattern_trace(
    goal_id: str,
    patterns: dict[str, list[str]],
    latency_ms: float,
) -> dict[str, Any]:
    return {
        "type": "pattern_trace",
        "goal_id": goal_id,
        "patterns": patterns,
        "assembly_latency_ms": latency_ms,
    }
