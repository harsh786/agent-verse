from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.orchestration.decision_trace import DecisionTrace
    from app.orchestration.runtime_profile import GoalRuntimeProfile


@dataclass
class ExplanationBundle:
    goal_id: str
    why_this_model: str = ""
    why_this_rag: str = ""
    why_this_guardrail: str = ""
    why_this_tool: str = ""
    why_this_fallback: str = ""
    unavailable_patterns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "why_this_model": self.why_this_model,
            "why_this_rag": self.why_this_rag,
            "why_this_guardrail": self.why_this_guardrail,
            "why_this_tool": self.why_this_tool,
            "why_this_fallback": self.why_this_fallback,
            "unavailable_patterns": self.unavailable_patterns,
        }


class DecisionExplainer:
    def explain(
        self,
        profile: GoalRuntimeProfile,
        trace: DecisionTrace,
    ) -> ExplanationBundle:
        bundle = ExplanationBundle(goal_id=profile.goal_id)
        for decision in trace.decisions:
            dim = decision.dimension
            reason = decision.reason
            if "model" in dim:
                bundle.why_this_model = (
                    f"Model tier '{decision.selected}' selected because: {reason}"
                )
            elif "rag" in dim:
                bundle.why_this_rag = (
                    f"RAG strategy '{decision.selected}' selected because: {reason}"
                )
        security = profile.security
        if security.hitl_required:
            bundle.why_this_guardrail = (
                f"HITL+strict guardrails: risk={profile.properties.risk.value}, "
                f"audit={security.audit_level}"
            )
        else:
            bundle.why_this_guardrail = f"Default guardrails: risk={profile.properties.risk.value}"
        if profile.rag_strategy.web_fallback_enabled:
            bundle.why_this_fallback = "Web fallback: KB empty/sparse or web signals detected"
        return bundle
