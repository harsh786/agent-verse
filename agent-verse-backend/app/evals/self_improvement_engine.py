"""SelfImprovementEngine — translates scorecard results into improvement actions."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.evals.runtime_scorecard import ScorecardResult
    from app.orchestration.runtime_profile import GoalRuntimeProfile
    from app.agent.state import AgentState


class ImprovementAction(str, enum.Enum):
    UPDATE_PROMPT_VARIANT = "update_prompt_variant"
    UPDATE_MODEL_ROUTING = "update_model_routing"
    UPDATE_RAG_STRATEGY = "update_rag_strategy"
    STORE_REFLEXION_LESSON = "store_reflexion_lesson"
    BLACKLIST_TOOL_PATTERN = "blacklist_tool_pattern"
    CREATE_REGRESSION_CASE = "create_regression_case"


@dataclass
class ImprovementDecision:
    action_type: ImprovementAction
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


class SelfImprovementEngine:
    """Decides improvement actions from scorecard results."""

    def decide_actions(
        self,
        scorecard: "ScorecardResult",
        profile: "GoalRuntimeProfile",
        state: "AgentState | None" = None,
    ) -> list[ImprovementDecision]:
        actions: list[ImprovementDecision] = []
        scores = scorecard.scores
        threshold = profile.eval_config.score_threshold

        if scorecard.overall_score >= threshold:
            return []

        if scores.get("rag_quality", 1.0) < 0.5 or scores.get("retrieval_confidence", 1.0) < 0.4:
            actions.append(ImprovementDecision(
                action_type=ImprovementAction.UPDATE_RAG_STRATEGY,
                reason=f"rag_quality={scores.get('rag_quality', 0):.2f} below 0.5",
                metadata={"current_rag_strategy": profile.rag_strategy.strategy},
            ))

        if scores.get("goal_success", 1.0) < 0.7 or scores.get("tool_success_rate", 1.0) < 0.5:
            if state and (state.verification_feedback or "").strip():
                actions.append(ImprovementDecision(
                    action_type=ImprovementAction.STORE_REFLEXION_LESSON,
                    reason="goal failed with actionable feedback",
                    metadata={"feedback": (state.verification_feedback or "")[:200]},
                ))
            actions.append(ImprovementDecision(
                action_type=ImprovementAction.UPDATE_PROMPT_VARIANT,
                reason=f"goal_success={scores.get('goal_success', 0):.2f} below 0.7",
            ))

        if scores.get("tool_success_rate", 1.0) < 0.3:
            actions.append(ImprovementDecision(
                action_type=ImprovementAction.BLACKLIST_TOOL_PATTERN,
                reason=f"tool_success_rate={scores.get('tool_success_rate', 0):.2f} critically low",
            ))

        if scores.get("cost_efficiency", 1.0) < 0.3 or scores.get("latency", 1.0) < 0.3:
            actions.append(ImprovementDecision(
                action_type=ImprovementAction.UPDATE_MODEL_ROUTING,
                reason="cost/latency score critically low",
            ))

        if scorecard.overall_score < 0.4:
            actions.append(ImprovementDecision(
                action_type=ImprovementAction.CREATE_REGRESSION_CASE,
                reason=f"overall_score={scorecard.overall_score:.2f} below 0.4",
            ))

        return actions
