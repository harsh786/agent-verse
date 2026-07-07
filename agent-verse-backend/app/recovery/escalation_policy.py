"""EscalationPolicy — determines when to escalate a failed goal to human."""
from __future__ import annotations
from dataclasses import dataclass

from app.recovery.failure_classifier import FailureClass, FailureResult


@dataclass
class EscalationDecision:
    should_escalate: bool
    escalation_level: str  # "warning" | "human_review" | "immediate"
    reason: str


class EscalationPolicy:
    def decide(
        self, failure: FailureResult, attempt_count: int = 1
    ) -> EscalationDecision:
        if failure.failure_class == FailureClass.SAFETY_VIOLATION:
            return EscalationDecision(
                True, "immediate", "safety violation requires immediate review"
            )
        if failure.failure_class == FailureClass.POLICY_REJECTION:
            return EscalationDecision(
                True, "human_review", "policy rejection needs human override"
            )
        if attempt_count >= 3:
            return EscalationDecision(
                True, "human_review", f"exhausted {attempt_count} attempts"
            )
        if failure.failure_class == FailureClass.USER_AMBIGUITY:
            return EscalationDecision(True, "human_review", "goal needs clarification")
        return EscalationDecision(False, "none", "recoverable failure — retry eligible")
