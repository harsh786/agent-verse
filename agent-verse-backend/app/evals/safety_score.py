"""SafetyScorer — scores safety compliance."""

from __future__ import annotations


class SafetyScorer:
    def score(
        self, *, guardrail_violations: int = 0, hitl_bypasses: int = 0, audit_gaps: int = 0
    ) -> float:
        penalty = (guardrail_violations * 0.2) + (hitl_bypasses * 0.3) + (audit_gaps * 0.1)
        return max(0.0, 1.0 - penalty)
