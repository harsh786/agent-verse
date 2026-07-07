"""RegressionGate — creates regression candidates from important failures."""
from __future__ import annotations
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.state import AgentState
    from app.evals.runtime_scorecard import ScorecardResult
    from app.orchestration.runtime_profile import GoalRuntimeProfile

_FAILURE_THRESHOLD = 0.6


class RegressionGate:
    def __init__(self, threshold: float = _FAILURE_THRESHOLD) -> None:
        self._threshold = threshold

    def maybe_create_regression(self, *, state: "AgentState", scorecard: "ScorecardResult",
                                 profile: "GoalRuntimeProfile") -> dict[str, Any] | None:
        if scorecard.overall_score >= self._threshold:
            return None
        from app.agent.state import GoalStatus
        if state.status not in (GoalStatus.FAILED, GoalStatus.COMPLETE):
            return None
        return {
            "goal_id": state.goal_id, "goal_text": state.goal[:200],
            "tenant_id": state.tenant_ctx.tenant_id,
            "overall_score": scorecard.overall_score, "scores": scorecard.scores,
            "status": state.status.value,
            "improvement_suggestions": scorecard.improvement_suggestions,
            "profile_id": profile.profile_id,
        }
