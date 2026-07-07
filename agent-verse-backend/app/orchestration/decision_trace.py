"""DecisionTrace — serializable record of every strategy selection decision."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SelectionDecision:
    selector: str
    dimension: str
    selected: Any
    reason: str
    alternatives: list[Any] = field(default_factory=list)
    latency_ms: float = 0.0


@dataclass
class DecisionTrace:
    goal_id: str
    tenant_id: str
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    decisions: list[SelectionDecision] = field(default_factory=list)
    total_latency_ms: float = 0.0
    created_at: float = field(default_factory=time.time)

    def add(
        self,
        selector: str,
        dimension: str,
        selected: Any,
        reason: str,
        alternatives: list[Any] | None = None,
        latency_ms: float = 0.0,
    ) -> None:
        self.decisions.append(
            SelectionDecision(
                selector=selector,
                dimension=dimension,
                selected=selected,
                reason=reason,
                alternatives=alternatives or [],
                latency_ms=latency_ms,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "goal_id": self.goal_id,
            "tenant_id": self.tenant_id,
            "total_latency_ms": self.total_latency_ms,
            "decisions": [
                {
                    "selector": d.selector,
                    "dimension": d.dimension,
                    "selected": d.selected if isinstance(d.selected, (str, int, float, bool, list, dict, type(None))) else str(d.selected),
                    "reason": d.reason,
                    "alternatives": [a if isinstance(a, (str, int, float, bool)) else str(a) for a in d.alternatives],
                    "latency_ms": d.latency_ms,
                }
                for d in self.decisions
            ],
        }

    def to_sse_event(self) -> dict[str, Any]:
        return {
            "type": "runtime_profile_selected",
            "goal_id": self.goal_id,
            "trace_id": self.trace_id,
            "decisions": self.to_dict()["decisions"],
            "total_latency_ms": self.total_latency_ms,
        }
