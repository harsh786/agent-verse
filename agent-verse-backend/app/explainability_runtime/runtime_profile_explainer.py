from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile


class RuntimeProfileExplainer:
    def summarize(self, profile: GoalRuntimeProfile) -> str:
        props = profile.properties
        security = profile.security
        rag = profile.rag_strategy
        lines = [
            f"Goal: {props.raw_goal[:80]}",
            f"Complexity: {props.complexity.value} | Risk: {props.risk.value}",
            f"Agent patterns: {profile.agent_patterns.reasoning}",
            f"RAG strategy: {rag.strategy} (sources: {rag.sources})",
            f"Model tier: {profile.model_plan.cost_class} / {profile.model_plan.latency_class}",
            f"HITL required: {security.hitl_required} | Audit: {security.audit_level}",
        ]
        return "\n".join(lines)
