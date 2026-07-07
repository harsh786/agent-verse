from __future__ import annotations
from typing import Any


def emit_model_trace(
    goal_id: str,
    planner: str,
    executor: str,
    verifier: str,
    tier: str,
) -> dict[str, Any]:
    return {
        "type": "model_trace",
        "goal_id": goal_id,
        "planner": planner,
        "executor": executor,
        "verifier": verifier,
        "tier": tier,
    }
