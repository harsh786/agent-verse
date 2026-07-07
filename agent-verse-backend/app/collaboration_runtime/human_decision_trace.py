from __future__ import annotations
from typing import Any


class HumanDecisionTrace:
    def __init__(self, goal_id: str) -> None:
        self.goal_id = goal_id
        self.decisions: list[dict[str, Any]] = []

    def record(
        self,
        decision_type: str,
        question: str,
        human_response: str,
        latency_seconds: float,
    ) -> None:
        self.decisions.append(
            {
                "decision_type": decision_type,
                "question": question,
                "human_response": human_response,
                "latency_seconds": latency_seconds,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {"goal_id": self.goal_id, "decisions": self.decisions}
