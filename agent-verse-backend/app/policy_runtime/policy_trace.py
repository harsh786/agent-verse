"""PolicyTrace — observability trace for policy compilation decisions.

Records every policy constraint and enforcement decision for audit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PolicyDecision:
    dimension: str  # "audit_level" | "max_cost" | "denied_capabilities" | etc.
    value: Any
    reason: str
    source: str = ""  # "risk_level" | "plan_tier" | "compliance_tag"


@dataclass
class PolicyTrace:
    """Trace of PolicyCompiler decisions for a goal execution."""

    goal_id: str
    tenant_id: str
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    decisions: list[PolicyDecision] = field(default_factory=list)

    def record(self, dimension: str, value: Any, reason: str, source: str = "") -> None:
        self.decisions.append(
            PolicyDecision(
                dimension=dimension,
                value=value,
                reason=reason,
                source=source,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "goal_id": self.goal_id,
            "tenant_id": self.tenant_id,
            "decisions": [
                {
                    "dimension": d.dimension,
                    "value": str(d.value),
                    "reason": d.reason,
                    "source": d.source,
                }
                for d in self.decisions
            ],
        }
