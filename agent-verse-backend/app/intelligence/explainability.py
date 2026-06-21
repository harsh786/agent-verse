"""Explainability — DecisionTrace captures why an action was taken.

Every tool call produces a DecisionTrace that records:
  - action: what was done
  - reasoning: why (LLM reasoning chain)
  - evidence: supporting facts from context (RAG hits, previous outputs)
  - alternatives: other approaches considered (and why they were not chosen)
  - confidence: 0.0-1.0 estimate from the LLM
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DecisionTrace:
    action: str
    reasoning: str
    evidence: list[str]
    alternatives: list[str]
    confidence: float
    trace_id: str = field(default_factory=lambda: __import__("uuid").uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "action": self.action,
            "reasoning": self.reasoning,
            "evidence": self.evidence,
            "alternatives": self.alternatives,
            "confidence": self.confidence,
        }
