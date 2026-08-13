from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.plan_runtime.plan_cost_estimator import PlanCostEstimator
from app.plan_runtime.plan_risk_analyzer import PlanRiskAnalyzer

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
            "missing_permissions": self.missing_permissions,
            "missing_context": self.missing_context,
            "warnings": self.warnings,
            "findings": self.findings,
        }


class PlanVerifier:
    def __init__(self) -> None:
        self._risk = PlanRiskAnalyzer()
        self._cost = PlanCostEstimator()

    def verify(self, *, plan: list[str], profile: GoalRuntimeProfile) -> PlanVerificationResult:
        warnings: list[str] = []
        if not plan:
            return PlanVerificationResult(
                feasible=False,
                risk_level="unknown",
                estimated_cost_usd=0.0,
                requires_hitl=False,
                safe_to_execute=False,
                findings=["Empty plan"],
            )
        if len(plan) > 50:
            warnings.append(f"Plan has {len(plan)} steps — consider sub-goals")
        risk_level, findings = self._risk.analyze(plan)
        cost_est = self._cost.estimate(plan, model_cost_class=profile.model_plan.cost_class)
        hitl_required = profile.security.hitl_required or risk_level in ("high", "critical")
        safe = risk_level not in ("critical",) or hitl_required
        return PlanVerificationResult(
            feasible=True,
            risk_level=risk_level,
            estimated_cost_usd=cost_est.estimated_cost_usd,
            requires_hitl=hitl_required,
            safe_to_execute=safe,
            warnings=warnings,
            findings=findings,
        )

    def verify_pre_execution(
        self,
        *,
        plan: list[str],
        profile: GoalRuntimeProfile,
        required_permissions: frozenset[str] = frozenset(),
        granted_permissions: frozenset[str] = frozenset(),
        required_context: frozenset[str] = frozenset(),
        available_context: frozenset[str] = frozenset(),
        data_classes: frozenset[str] = frozenset(),
        allowed_data_classes: frozenset[str] = frozenset({"public", "internal"}),
        budget_usd: float | None = None,
        sandbox_ready: bool = True,
        hitl_approved: bool = False,
    ) -> PlanVerificationResult:
        base = self.verify(plan=plan, profile=profile)
        missing_permissions = sorted(required_permissions - granted_permissions)
        missing_context = sorted(required_context - available_context)
        findings = list(base.findings)
        if missing_permissions:
            findings.append("Missing required permissions")
        if missing_context:
            findings.append("Missing required context")
        if not data_classes <= allowed_data_classes:
            findings.append("Data classification denied")
        if budget_usd is not None and base.estimated_cost_usd > budget_usd:
            findings.append("Estimated cost exceeds budget")
        if profile.security.sandbox_required and not sandbox_ready:
            findings.append("Required sandbox is unavailable")
        if base.requires_hitl and not hitl_approved:
            findings.append("Human approval required")
        safe = base.safe_to_execute and not any(
            item in findings
            for item in (
                "Missing required permissions",
                "Missing required context",
                "Data classification denied",
                "Estimated cost exceeds budget",
                "Required sandbox is unavailable",
                "Human approval required",
            )
        )
        return PlanVerificationResult(
            feasible=base.feasible and not missing_permissions and not missing_context,
            risk_level=base.risk_level,
            estimated_cost_usd=base.estimated_cost_usd,
            requires_hitl=base.requires_hitl,
            safe_to_execute=safe,
            missing_permissions=missing_permissions,
            missing_context=missing_context,
            warnings=base.warnings,
            findings=findings,
        )
