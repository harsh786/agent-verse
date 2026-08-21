from __future__ import annotations

from typing import Any


def emit_rag_trace(
    goal_id: str,
    strategy: str,
    result_count: int,
    confidence: float,
) -> dict[str, Any]:
    return {
        "type": "rag_trace",
        "goal_id": goal_id,
        "strategy": strategy,
        "result_count": result_count,
        "confidence": confidence,
    }
