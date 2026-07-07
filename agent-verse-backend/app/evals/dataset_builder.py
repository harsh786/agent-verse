"""EvalDatasetBuilder — converts important failures into reusable golden tasks."""
from __future__ import annotations
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.state import AgentState

_CANDIDATE_THRESHOLD = 0.6


class EvalDatasetBuilder:
    def maybe_create(
        self, *, state: "AgentState", score: float
    ) -> dict[str, Any] | None:
        if score >= _CANDIDATE_THRESHOLD:
            return None
        from app.agent.state import GoalStatus

        return {
            "goal_id": state.goal_id,
            "goal_text": state.goal[:500],
            "tenant_id": state.tenant_ctx.tenant_id,
            "final_status": state.status.value,
            "score": score,
            "expected_behavior": self._infer_expected(state),
            "regression_candidate": True,
        }

    def _infer_expected(self, state: "AgentState") -> str:
        from app.agent.state import GoalStatus

        if state.status == GoalStatus.FAILED:
            return "Goal should complete successfully with correct output"
        return "Goal should complete with higher quality score"
