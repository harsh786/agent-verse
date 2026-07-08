"""RuntimeScorecard — produces a 9-dimension eval scorecard for every goal."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING
from app.evals.goal_score import GoalScorer
from app.evals.rag_score import RAGScorer
from app.evals.safety_score import SafetyScorer
from app.evals.model_score import ModelScorer
from app.evals.agent_score import AgentScorer

if TYPE_CHECKING:
    from app.agent.state import AgentState
    from app.orchestration.runtime_profile import GoalRuntimeProfile
    from app.rag.agentic.retriever_tool import RetrievalResult


@dataclass
class ScorecardResult:
    goal_id: str
    scores: dict[str, float]
    overall_score: float
    improvement_suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id, "scores": self.scores,
            "overall_score": self.overall_score,
            "improvement_suggestions": self.improvement_suggestions,
        }


class RuntimeScorecard:
    def __init__(self) -> None:
        self._goal_scorer = GoalScorer()
        self._rag_scorer = RAGScorer()
        self._safety_scorer = SafetyScorer()
        self._model_scorer = ModelScorer()
        self._agent_scorer = AgentScorer()

    def score(self, *, state: "AgentState", profile: "GoalRuntimeProfile",
              retrieval_result: Any = None, cost_usd: float = 0.0,
              latency_ms: float = 0.0, guardrail_violations: int = 0) -> ScorecardResult:
        goal_s = self._goal_scorer.score(state)
        rag_s = self._rag_scorer.score(retrieval_result)
        safety_s = self._safety_scorer.score(guardrail_violations=guardrail_violations)
        cost_s = self._model_scorer.score_cost(profile, state)
        latency_s = self._model_scorer.score_latency(state)
        # Preserve blended score for backward-compat callers that still need model_s
        model_s = cost_s
        grounding_s = self._agent_scorer.score_grounding(state)
        citation_s = self._agent_scorer.score_citation_quality(state)
        retrieval_conf = getattr(retrieval_result, "confidence", 0.5) if retrieval_result else 0.5
        tool_s = self._agent_scorer.score_tool_success_rate(state)

        scores = {
            "goal_success": round(goal_s, 3),
            "rag_quality": round(rag_s, 3),
            "safety": round(safety_s, 3),
            "latency": round(latency_s, 3),
            "cost_efficiency": round(model_s, 3),
            "grounding": round(grounding_s, 3),
            "citation_quality": round(citation_s, 3),
            "retrieval_confidence": round(retrieval_conf, 3),
            "tool_success_rate": round(tool_s, 3),
        }

        overall = (goal_s * 0.30 + rag_s * 0.15 + safety_s * 0.15 +
                   grounding_s * 0.10 + tool_s * 0.10 + citation_s * 0.05 +
                   retrieval_conf * 0.05 + latency_s * 0.05 + model_s * 0.05)

        suggestions = []
        if rag_s < 0.5:
            suggestions.append("Consider switching RAG strategy — low retrieval confidence")
        if goal_s < 0.7 and state.iterations > 15:
            suggestions.append("High iteration count — consider goal decomposition")
        if safety_s < 1.0:
            suggestions.append("Safety violations detected — review guardrail configuration")
        if grounding_s < 0.7:
            suggestions.append("High hallucination rate — improve grounding or retrieval")
        if tool_s < 0.5:
            suggestions.append("Low tool success rate — check tool trust scores and circuit breakers")

        return ScorecardResult(goal_id=state.goal_id, scores=scores,
                               overall_score=round(overall, 3),
                               improvement_suggestions=suggestions)
