from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING
from app.plan_runtime.plan_risk_analyzer import PlanRiskAnalyzer
from app.plan_runtime.plan_cost_estimator import PlanCostEstimator

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile


@dataclass
class PlanVerificationResult:
    feasible: bool
    risk_level: str
    estimated_cost_usd: float
    requires_hitl: bool
    safe_to_execute: bool
    missing_permissions: list[str] = field(default_factory=list)
    missing_context: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feasible": self.feasible,
            "risk_level": self.risk_level,
            "estimated_cost_usd": self.estimated_cost_usd,
            "requires_hitl": self.requires_hitl,
            "safe_to_execute": self.safe_to_execute,
            "warnings": self.warnings,
            "findings": self.findings,
        }


class PlanVerifier:
    def __init__(self) -> None:
        self._risk = PlanRiskAnalyzer()
        self._cost = PlanCostEstimator()

    def verify(self, *, plan: list[str], profile: "GoalRuntimeProfile") -> PlanVerificationResult:
        warnings: list[str] = []
        if not plan:
            return PlanVerificationResult(
                feasible=False, risk_level="unknown",
                estimated_cost_usd=0.0, requires_hitl=False,
                safe_to_execute=False, findings=["Empty plan"],
            )
        if len(plan) > 50:
            warnings.append(f"Plan has {len(plan)} steps — consider sub-goals")
        risk_level, findings = self._risk.analyze(plan)
        cost_est = self._cost.estimate(plan, model_cost_class=profile.model_plan.cost_class)
        hitl_required = profile.security.hitl_required or risk_level in ("high", "critical")
        safe = risk_level not in ("critical",) or hitl_required
        return PlanVerificationResult(
            feasible=True, risk_level=risk_level,
            estimated_cost_usd=cost_est.estimated_cost_usd,
            requires_hitl=hitl_required, safe_to_execute=safe,
            warnings=warnings, findings=findings,
        )
