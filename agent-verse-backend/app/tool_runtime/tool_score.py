from __future__ import annotations
from dataclasses import dataclass
from app.tool_runtime.tool_trust_store import ToolTrustStore

_CONSECUTIVE_FAILURES_FOR_OPEN = 5


@dataclass
class ToolTrustProfile:
    tool_name: str
    success_rate: float
    p95_latency_ms: float
    safety_incidents: int
    trust_score: float
    circuit_state: str
    call_count: int


class ToolScorer:
    def __init__(self, trust_store: ToolTrustStore) -> None:
        self._store = trust_store

    def score(self, tool_name: str) -> ToolTrustProfile:
        history = self._store.get_history(tool_name)
        if not history:
            return ToolTrustProfile(
                tool_name=tool_name, success_rate=1.0, p95_latency_ms=500.0,
                safety_incidents=0, trust_score=0.5,
                circuit_state="closed", call_count=0,
            )
        successes = sum(1 for h in history if h["success"])
        success_rate = successes / len(history)
        latencies = sorted(h["latency_ms"] for h in history)
        p95_idx = int(len(latencies) * 0.95)
        p95_latency = latencies[min(p95_idx, len(latencies) - 1)]
        consecutive_failures = 0
        for h in reversed(history):
            if not h["success"]:
                consecutive_failures += 1
            else:
                break
        circuit_state = "open" if consecutive_failures >= _CONSECUTIVE_FAILURES_FOR_OPEN else "closed"
        latency_score = max(0.0, 1.0 - p95_latency / 10000.0)
        trust_score = 0.7 * success_rate + 0.3 * latency_score
        if circuit_state == "open":
            trust_score = min(trust_score, 0.2)
        return ToolTrustProfile(
            tool_name=tool_name, success_rate=success_rate, p95_latency_ms=p95_latency,
            safety_incidents=0, trust_score=round(trust_score, 3),
            circuit_state=circuit_state, call_count=len(history),
        )
