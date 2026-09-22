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
        """Append one retrieval step. Tolerant of malformed inputs (e.g. a
        ``None`` query from an upstream partial-data path) so a bad call
        never raises out of trace recording."""
        safe_query = "" if query is None else str(query)[:200]
        self.steps.append(
            {
                "strategy": strategy,
                "query": safe_query,
                "result_count": result_count,
                "confidence": confidence,
                "latency_ms": latency_ms,
            }
        )

    def to_sse_event(self) -> dict[str, Any]:
        """Build the SSE payload. Defensive against partial/malformed step
        dicts (missing or non-numeric ``result_count``, non-dict entries) so
        a corrupt step never breaks emission of the whole trace."""
        last = self.steps[-1] if self.steps and isinstance(self.steps[-1], dict) else {}

        def _result_count(step: Any) -> int:
            if not isinstance(step, dict):
                return 0
            try:
                return int(step.get("result_count") or 0)
            except (TypeError, ValueError):
                return 0

        return {
            "type": "rag_strategy_selected",
            "goal_id": self.goal_id,
            "trace_id": self.trace_id,
            "strategy": last.get("strategy", "unknown"),
            "steps": len(self.steps),
            "total_results": sum(_result_count(s) for s in self.steps),
        }
