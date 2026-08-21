"""RAGTrace — structured observability trace for agentic retrieval."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RAGTrace:
    goal_id: str
    tenant_id: str
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    steps: list[dict[str, Any]] = field(default_factory=list)

    def record_retrieval(
        self,
        strategy: str,
        query: str,
        result_count: int,
        confidence: float,
        latency_ms: float,
    ) -> None:
        self.steps.append(
            {
                "strategy": strategy,
                "query": query[:200],
                "result_count": result_count,
                "confidence": confidence,
                "latency_ms": latency_ms,
            }
        )

    def to_sse_event(self) -> dict[str, Any]:
        last = self.steps[-1] if self.steps else {}
        return {
            "type": "rag_strategy_selected",
            "goal_id": self.goal_id,
            "trace_id": self.trace_id,
            "strategy": last.get("strategy", "unknown"),
            "steps": len(self.steps),
            "total_results": sum(s["result_count"] for s in self.steps),
        }
