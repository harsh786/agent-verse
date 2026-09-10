"""
part 11 — Goal Refinement Pipeline.

CEO Agent refines a raw user goal into a structured OrgMission spec:
  raw_goal → decompose → requirements → success_criteria → constraints → risks

This is the first step in the Goal → Mission → Team → Execution pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

from opentelemetry import trace

from app.observability.logging import get_logger

_log = get_logger(__name__)
_tracer = trace.get_tracer(__name__)


@dataclass
class RefinedMissionSpec:
    """Structured mission spec produced by goal refinement."""

    original_goal: str
    refined_goal: str
    requirements: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    risks: list[str] = field(default_factory=list)
    risk_level: str = "medium"  # low | medium | high | critical
    autonomy_level: int = 3
    estimated_budget_usd: float = 0.0
    estimated_duration_hours: float = 0.0
    departments_involved: list[str] = field(default_factory=list)
    refinement_confidence: float = 0.80
    refined_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class GoalRefinementPipeline:
    """
    Transforms a raw user goal into a structured OrgMission spec.

    Steps:
      1. Sanitize goal (injection detection)
      2. Decompose into strategic requirements
      3. Identify success criteria
      4. Extract constraints (budget, timeline, compliance)
      5. Identify risks
      6. Determine autonomy level
      7. Produce RefinedMissionSpec
    """

    # Dangerous goal pattern detection (PART 19 / PART 48)
    INJECTION_PATTERNS: ClassVar[list[str]] = [
        r"ignore.*above.*instructions",
        r"pretend.*you.*are",
        r"jailbreak",
        r"delete.*all.*data",
        r"drop.*table",
        r"rm\s+-rf",
        r"bypass.*security",
        r"override.*policy",
    ]

    # Goal keywords → departments
    DEPT_HEURISTICS: ClassVar[dict[str, list[str]]] = {
        "strategy": ["strategy", "strategic", "competitive", "market", "vision", "plan"],
        "engineering": ["code", "build", "develop", "engineer", "api", "software", "architecture"],
        "marketing": ["campaign", "brand", "content", "seo", "social", "launch", "promote"],
        "sales": ["revenue", "leads", "sales", "pipeline", "deal", "customer acquisition"],
        "finance": ["budget", "cost", "revenue", "financial", "forecast", "spend", "invoice"],
        "legal": ["legal", "compliance", "gdpr", "contract", "regulatory", "privacy"],
        "hr": ["hire", "recruit", "talent", "employee", "onboard", "culture"],
        "data": ["data", "analytics", "dashboard", "reporting", "metrics", "kpi"],
        "research": ["research", "analyze", "investigate", "study", "literature"],
        "security": ["security", "vulnerability", "threat", "breach", "pentest"],
        "operations": ["process", "workflow", "efficiency", "optimize", "automate"],
        "customer_success": ["customer", "support", "churn", "retention", "satisfaction"],
        "design": ["design", "ux", "ui", "branding", "creative", "visual"],
    }

    # Risk keywords
    HIGH_RISK_KEYWORDS: ClassVar[list[str]] = [
        "production",
        "deploy",
        "delete",
        "drop",
        "migrate",
        "external publish",
        "legal agreement",
        "press release",
        "financial transfer",
        "mass email",
    ]

    def refine(
        self, raw_goal: str, org_context: dict[str, Any] | None = None
    ) -> RefinedMissionSpec:
        """
        Synchronous goal refinement using heuristics.
        For LLM-assisted refinement, use refine_with_llm() instead.
        """
        with _tracer.start_as_current_span("goal_refinement.refine") as span:
            span.set_attribute("goal_length", len(raw_goal))

            # Step 1: Injection detection
            self._check_injection(raw_goal)

            # Step 2: Sanitize
            sanitized = self._sanitize(raw_goal)

            # Step 3: Decompose
            requirements = self._extract_requirements(sanitized)
            success_criteria = self._extract_success_criteria(sanitized, requirements)
            constraints = self._extract_constraints(sanitized, org_context or {})
            risks = self._extract_risks(sanitized)

            # Step 4: Determine risk level + autonomy
            risk_level = self._determine_risk_level(sanitized, risks)
            autonomy_level = self._determine_autonomy_level(risk_level)

            # Step 5: Identify departments
            depts = self._identify_departments(sanitized)

            # Step 6: Estimate
            est_budget, est_hours = self._estimate(depts, requirements, constraints)

            # Step 7: Produce refined goal string
            refined_goal = self._produce_refined_goal(sanitized, requirements, constraints)

            spec = RefinedMissionSpec(
                original_goal=raw_goal,
                refined_goal=refined_goal,
                requirements=requirements,
                success_criteria=success_criteria,
                constraints=constraints,
                risks=risks,
                risk_level=risk_level,
                autonomy_level=autonomy_level,
                estimated_budget_usd=est_budget,
                estimated_duration_hours=est_hours,
                departments_involved=depts,
                refinement_confidence=0.75,  # heuristic — LLM gives higher
            )

            span.set_attribute("risk_level", risk_level)
            span.set_attribute("autonomy_level", autonomy_level)
            span.set_attribute("departments_count", len(depts))
            _log.info(
                "goal_refinement.complete",
                risk=risk_level,
                depts=depts,
                requirements_count=len(requirements),
            )
            return spec

    # ── Private helpers ──────────────────────────────────────────────────────

    def _check_injection(self, goal: str) -> None:
        """Raise ValueError if goal contains injection patterns."""
        g_lower = goal.lower()
        for pattern in self.INJECTION_PATTERNS:
            if re.search(pattern, g_lower):
                raise ValueError(
                    f"Goal rejected: potential injection pattern detected. Pattern: {pattern}"
                )

    def _sanitize(self, goal: str) -> str:
        """Remove leading/trailing whitespace, collapse multiple spaces."""
        return " ".join(goal.split())

    def _extract_requirements(self, goal: str) -> list[str]:
        """Heuristic: split on conjunctions and numbered items."""
        reqs: list[str] = []
        # Split on common separators
        for sep in [" and ", " then ", ";", ". "]:
            if sep in goal.lower():
                parts = goal.split(sep)
                reqs.extend(p.strip().rstrip(".") for p in parts if len(p.strip()) > 10)
                break
        if not reqs:
            reqs = [goal.strip()]
        # Deduplicate — seen.add(r) returns None, so the `not seen.add(r)` trick
        # always evaluates True. Use an explicit loop instead.
        seen: set[str] = set()
        unique_reqs: list[str] = []
        for r in reqs:
            if r not in seen:
                seen.add(r)
                unique_reqs.append(r)
        return unique_reqs[:8]  # max 8

    def _extract_success_criteria(self, goal: str, requirements: list[str]) -> list[str]:
        """Generate measurable success criteria from requirements."""
        criteria = []
        for req in requirements[:4]:
            if any(k in req.lower() for k in ["research", "analyze", "investigate"]):
                criteria.append(f"Comprehensive report on: {req[:60]}")
            elif any(k in req.lower() for k in ["build", "develop", "create"]):
                criteria.append(f"Working implementation of: {req[:60]}")
            elif any(k in req.lower() for k in ["launch", "deploy", "publish"]):
                criteria.append(f"Successful launch of: {req[:60]}")
            else:
                criteria.append(f"Measurable outcome for: {req[:60]}")
        return criteria or [f"Goal achieved: {goal[:80]}"]

    def _extract_constraints(self, goal: str, org_context: dict) -> dict[str, Any]:
        constraints: dict[str, Any] = {}
        g_lower = goal.lower()

        # Timeline detection
        if "week" in g_lower:
            constraints["timeline_days"] = 7
        elif "month" in g_lower:
            constraints["timeline_days"] = 30
        elif "day" in g_lower or "today" in g_lower:
            constraints["timeline_days"] = 1

        # Budget from org context
        if budget := org_context.get("monthly_budget_usd"):
            constraints["max_budget_usd"] = float(budget) * 0.1  # 10% per mission default

        # Compliance constraints
        if any(k in g_lower for k in ["germany", "eu", "europe"]):
            constraints["compliance"] = ["GDPR"]
        if any(k in g_lower for k in ["hipaa", "medical", "health"]):
            constraints["compliance"] = [*constraints.get("compliance", []), "HIPAA"]

        return constraints

    def _extract_risks(self, goal: str) -> list[str]:
        risks = []
        g_lower = goal.lower()
        if any(k in g_lower for k in self.HIGH_RISK_KEYWORDS):
            risks.append("High-risk action detected — requires human approval gate")
        if "production" in g_lower:
            risks.append("Production system modification — test thoroughly first")
        if any(k in g_lower for k in ["external", "public", "publish"]):
            risks.append("External-facing action — brand and legal review required")
        if any(k in g_lower for k in ["delete", "remove", "drop"]):
            risks.append("Destructive operation — requires explicit confirmation")
        return risks

    def _determine_risk_level(self, goal: str, risks: list[str]) -> str:
        score = len(risks)
        g_lower = goal.lower()
        if any(
            k in g_lower for k in ["delete", "production", "legal agreement", "financial transfer"]
        ):
            score += 3
        if any(k in g_lower for k in ["external publish", "mass email", "press release"]):
            score += 2
        if score >= 4:
            return "critical"
        if score >= 3:
            return "high"
        if score >= 1:
            return "medium"
        return "low"

    def _determine_autonomy_level(self, risk_level: str) -> int:
        return {"low": 4, "medium": 3, "high": 2, "critical": 1}.get(risk_level, 3)

    def _identify_departments(self, goal: str) -> list[str]:
        g_lower = goal.lower()
        depts = []
        for dept, keywords in self.DEPT_HEURISTICS.items():
            if any(k in g_lower for k in keywords):
                depts.append(dept)
        return depts or ["strategy", "operations"]

    def _estimate(
        self, depts: list[str], requirements: list[str], constraints: dict
    ) -> tuple[float, float]:
        base_hours = max(4.0, len(requirements) * 2.0 + len(depts) * 1.5)
        base_cost = len(depts) * base_hours * 0.08  # $0.08 per agent-hour avg
        if td := constraints.get("timeline_days"):
            base_hours = min(base_hours, float(td) * 8)
        return round(base_cost, 2), round(base_hours, 1)

    def _produce_refined_goal(self, goal: str, requirements: list[str], constraints: dict) -> str:
        if len(requirements) <= 1:
            return goal
        req_str = "; ".join(requirements[:3])
        extra = ""
        if c := constraints.get("compliance"):
            extra += f" Compliance requirements: {', '.join(c)}."
        if td := constraints.get("timeline_days"):
            extra += f" Target timeline: {td} days."
        return f"{goal}{extra} Key requirements: {req_str}."


# Global singleton
goal_refinement_pipeline = GoalRefinementPipeline()
