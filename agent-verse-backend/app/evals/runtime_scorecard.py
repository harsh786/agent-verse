"""Truthful runtime scorecards derived only from observed execution evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from app.evals.agent_score import AgentScorer
from app.evals.goal_score import GoalScorer
from app.evals.model_score import ModelScorer
from app.evals.rag_score import RAGScorer
from app.evals.safety_score import SafetyScorer

if TYPE_CHECKING:
    from app.agent.state import AgentState
    from app.orchestration.runtime_profile import GoalRuntimeProfile

DimensionStatus = Literal["measured", "not_applicable", "unavailable", "legacy_unknown"]
EVALUATOR_VERSION = "runtime-scorecard-v2"

DIMENSION_WEIGHTS: dict[str, float] = {
    "goal_success": 0.30,
    "rag_quality": 0.15,
    "safety": 0.15,
    "grounding": 0.10,
    "tool_success_rate": 0.10,
    "citation_quality": 0.05,
    "retrieval_confidence": 0.05,
    "latency": 0.05,
    "cost_efficiency": 0.05,
}


@dataclass
class ScorecardResult:
    goal_id: str
    scores: dict[str, float]
    overall_score: float
    improvement_suggestions: list[str] = field(default_factory=list)
    dimension_status: dict[str, DimensionStatus] = field(default_factory=dict)
    evidence_references: dict[str, list[str]] = field(default_factory=dict)
    coverage: float = 0.0
    evaluator_version: str = EVALUATOR_VERSION
    primary_strategy_id: str = "unknown"
    primary_strategy_version: str = "unknown"
    auxiliary_strategy_versions: dict[str, str] = field(default_factory=dict)
    profile_id: str = "unknown"
    profile_version: int = 0
    strategy_execution_id: str = "legacy"
    correlation_id: str = ""
    weights: dict[str, float] = field(default_factory=lambda: dict(DIMENSION_WEIGHTS))

    def promotion_eligible(self, *, minimum_coverage: float = 0.9) -> bool:
        return self.coverage >= minimum_coverage

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "scores": self.scores,
            "overall_score": self.overall_score,
            "improvement_suggestions": self.improvement_suggestions,
            "dimension_status": self.dimension_status,
            "evidence_references": self.evidence_references,
            "coverage": self.coverage,
            "evaluator_version": self.evaluator_version,
            "primary_strategy_id": self.primary_strategy_id,
            "primary_strategy_version": self.primary_strategy_version,
            "auxiliary_strategy_versions": self.auxiliary_strategy_versions,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "strategy_execution_id": self.strategy_execution_id,
            "correlation_id": self.correlation_id,
        }


class RuntimeScorecard:
    def __init__(self) -> None:
        self._goal_scorer = GoalScorer()
        self._rag_scorer = RAGScorer()
        self._safety_scorer = SafetyScorer()
        self._model_scorer = ModelScorer()
        self._agent_scorer = AgentScorer()

    def score(
        self,
        *,
        state: AgentState,
        profile: GoalRuntimeProfile,
        retrieval_result: Any = None,
        cost_usd: float | None = None,
        latency_ms: float | None = None,
        guardrail_violations: int | None = None,
    ) -> ScorecardResult:
        context = state.context if isinstance(state.context, dict) else {}
        if cost_usd is not None:
            context["total_cost_usd"] = cost_usd
        if latency_ms is not None:
            context["_latency_ms"] = latency_ms

        scores: dict[str, float] = {}
        statuses: dict[str, DimensionStatus] = {}
        references: dict[str, list[str]] = {}

        def record(
            name: str,
            value: float | None,
            *,
            applicable: bool = True,
            refs: list[str] | None = None,
        ) -> None:
            if not applicable:
                statuses[name] = "not_applicable"
                references[name] = []
            elif value is None:
                statuses[name] = "unavailable"
                references[name] = []
            else:
                statuses[name] = "measured"
                scores[name] = round(max(0.0, min(1.0, value)), 3)
                references[name] = refs or []

        record("goal_success", self._goal_scorer.score(state), refs=["agent_state.status"])

        retrieval_required = bool(
            retrieval_result is not None
            or context.get("retrieval_required")
            or context.get("retrieval_attempted")
        )
        rag_score = self._rag_scorer.score(retrieval_result)
        retrieval_ref = str(
            context.get("retrieval_evidence_ref")
            or getattr(retrieval_result, "evidence_ref", "")
        )
        retrieval_refs = [retrieval_ref] if retrieval_ref else ["retrieval_result"]
        record(
            "rag_quality",
            rag_score,
            applicable=retrieval_required,
            refs=retrieval_refs,
        )
        confidence = None
        if retrieval_result is not None:
            confidence = float(
                retrieval_result.get("confidence", 0.0)
                if isinstance(retrieval_result, dict)
                else retrieval_result.confidence
            )
        record(
            "retrieval_confidence",
            confidence,
            applicable=retrieval_required,
            refs=retrieval_refs,
        )

        violation_count = guardrail_violations
        if violation_count is None and "guardrail_violations" in context:
            violation_count = int(context["guardrail_violations"])
        record(
            "safety",
            self._safety_scorer.score(guardrail_violations=violation_count)
            if violation_count is not None
            else None,
            refs=["guardrail_evidence"],
        )

        record(
            "cost_efficiency",
            self._model_scorer.score_cost(profile, state)
            if "total_cost_usd" in context or hasattr(state, "total_cost_usd")
            else None,
            refs=["cost_ledger"],
        )
        record(
            "latency",
            self._model_scorer.score_latency(state) if "_latency_ms" in context else None,
            refs=["execution_trace.latency_ms"],
        )

        grounding = self._agent_scorer.score_grounding(state)
        citation = self._agent_scorer.score_citation_quality(state)
        record(
            "grounding",
            grounding,
            applicable=retrieval_required,
            refs=["grounding_evidence"],
        )
        record(
            "citation_quality",
            citation,
            applicable=retrieval_required and profile.rag_strategy.citation_required,
            refs=["agent_state.provenance"],
        )

        has_tool_calls = any(bool(step.tool_calls) for step in state.steps)
        tools_required = bool(context.get("tools_required") or has_tool_calls)
        record(
            "tool_success_rate",
            self._agent_scorer.score_tool_success_rate(state),
            applicable=tools_required,
            refs=["step.tool_calls"],
        )

        available_weight = sum(DIMENSION_WEIGHTS[name] for name in scores)
        overall = (
            sum(scores[name] * DIMENSION_WEIGHTS[name] for name in scores)
            / available_weight
            if available_weight
            else 0.0
        )
        applicable = [name for name, status in statuses.items() if status != "not_applicable"]
        measured = [name for name in applicable if statuses[name] == "measured"]
        coverage = len(measured) / len(applicable) if applicable else 0.0

        suggestions: list[str] = []
        if statuses["rag_quality"] == "measured" and scores["rag_quality"] < 0.5:
            suggestions.append("Consider switching RAG strategy — low retrieval confidence")
        if scores["goal_success"] < 0.7 and state.iterations > 15:
            suggestions.append("High iteration count — consider goal decomposition")
        if statuses["safety"] == "measured" and scores["safety"] < 1.0:
            suggestions.append("Safety violations detected — review guardrail configuration")
        if statuses["grounding"] == "measured" and scores["grounding"] < 0.7:
            suggestions.append("High hallucination rate — improve grounding or retrieval")
        if (
            statuses["tool_success_rate"] == "measured"
            and scores["tool_success_rate"] < 0.5
        ):
            suggestions.append(
                "Low tool success rate — check tool trust scores and circuit breakers"
            )

        primary = profile.primary_strategy
        return ScorecardResult(
            goal_id=state.goal_id,
            scores=scores,
            overall_score=round(overall, 3),
            improvement_suggestions=suggestions,
            dimension_status=statuses,
            evidence_references=references,
            coverage=round(coverage, 3),
            primary_strategy_id=primary.strategy_id,
            primary_strategy_version=primary.adapter_version,
            auxiliary_strategy_versions={
                item.strategy_id: item.adapter_version
                for item in profile.auxiliary_strategies
            },
            profile_id=profile.profile_id,
            profile_version=profile.profile_version,
            strategy_execution_id=str(
                context.get("strategy_execution_id") or f"legacy:{state.goal_id}"
            ),
            correlation_id=str(context.get("correlation_id", "")),
        )
