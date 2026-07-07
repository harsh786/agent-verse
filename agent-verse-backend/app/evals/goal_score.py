"""GoalScorer — scores task completion and iteration efficiency."""
from __future__ import annotations
from app.agent.state import AgentState, GoalStatus


class GoalScorer:
    def score(self, state: AgentState) -> float:
        if state.status == GoalStatus.COMPLETE:
            base = 1.0
            excess = max(0, state.iterations - 5)
            efficiency_penalty = min(0.3, excess * 0.01)
            return max(0.0, base - efficiency_penalty)
        elif state.status == GoalStatus.FAILED:
            return 0.0
        elif state.status == GoalStatus.WAITING_HUMAN:
            return 0.5
        return 0.3
