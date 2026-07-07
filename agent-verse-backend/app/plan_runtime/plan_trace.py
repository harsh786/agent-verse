"""PlanTrace — observability trace for plan verification decisions.

Records every risk finding and verification decision for audit and observability.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlanTraceEntry:
    step: str
    risk_level: str
    findings: list[str] = field(default_factory=list)
    requires_hitl: bool = False


@dataclass
class PlanTrace:
    """Trace of PlanVerifier decisions for a goal execution."""
    goal_id: str
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    entries: list[PlanTraceEntry] = field(default_factory=list)
    overall_risk: str = "low"
    estimated_cost_usd: float = 0.0

    def record(
        self,
        step: str,
        risk_level: str,
        findings: list[str] | None = None,
        requires_hitl: bool = False,
    ) -> None:
        self.entries.append(PlanTraceEntry(
            step=step, risk_level=risk_level,
            findings=findings or [], requires_hitl=requires_hitl,
        ))
        # Update overall risk to highest seen
        order = ["low", "medium", "high", "critical"]
        if order.index(risk_level) > order.index(self.overall_risk):
            self.overall_risk = risk_level

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "goal_id": self.goal_id,
            "overall_risk": self.overall_risk,
            "estimated_cost_usd": self.estimated_cost_usd,
            "entries": [
                {"step": e.step, "risk_level": e.risk_level,
                 "findings": e.findings, "requires_hitl": e.requires_hitl}
                for e in self.entries
            ],
        }
