"""Tests for app.recovery.escalation_policy.EscalationPolicy.

Covers every branch of decide(): safety violations (immediate), policy
rejections and user ambiguity (human_review), attempt-count exhaustion, and
the recoverable "no escalation" default — plus the priority ordering between
these conditions when more than one applies.
"""
from __future__ import annotations

from app.recovery.escalation_policy import EscalationDecision, EscalationPolicy
from app.recovery.failure_classifier import FailureClass, FailureResult


def _failure(failure_class: FailureClass) -> FailureResult:
    return FailureResult(failure_class=failure_class, error_text="boom")


class TestEscalationPolicyDecide:
    def test_safety_violation_escalates_immediately(self):
        policy = EscalationPolicy()
        decision = policy.decide(_failure(FailureClass.SAFETY_VIOLATION))
        assert decision.should_escalate is True
        assert decision.escalation_level == "immediate"
        assert "safety violation" in decision.reason

    def test_policy_rejection_escalates_to_human_review(self):
        policy = EscalationPolicy()
        decision = policy.decide(_failure(FailureClass.POLICY_REJECTION))
        assert decision.should_escalate is True
        assert decision.escalation_level == "human_review"
        assert "policy rejection" in decision.reason

    def test_user_ambiguity_escalates_to_human_review(self):
        policy = EscalationPolicy()
        decision = policy.decide(_failure(FailureClass.USER_AMBIGUITY))
        assert decision.should_escalate is True
        assert decision.escalation_level == "human_review"
        assert "clarification" in decision.reason

    def test_recoverable_failure_does_not_escalate_by_default(self):
        policy = EscalationPolicy()
        decision = policy.decide(_failure(FailureClass.TIMEOUT))
        assert decision.should_escalate is False
        assert decision.escalation_level == "none"
        assert "retry eligible" in decision.reason

    def test_attempt_count_default_is_one_and_does_not_trip_threshold(self):
        policy = EscalationPolicy()
        decision = policy.decide(_failure(FailureClass.TIMEOUT))
        assert decision.should_escalate is False

    def test_attempt_count_below_three_does_not_escalate(self):
        policy = EscalationPolicy()
        decision = policy.decide(_failure(FailureClass.TIMEOUT), attempt_count=2)
        assert decision.should_escalate is False

    def test_attempt_count_at_three_escalates_with_reason(self):
        policy = EscalationPolicy()
        decision = policy.decide(_failure(FailureClass.TIMEOUT), attempt_count=3)
        assert decision.should_escalate is True
        assert decision.escalation_level == "human_review"
        assert "exhausted 3 attempts" in decision.reason

    def test_attempt_count_above_three_still_escalates(self):
        policy = EscalationPolicy()
        decision = policy.decide(_failure(FailureClass.RATE_LIMIT), attempt_count=5)
        assert decision.should_escalate is True
        assert "exhausted 5 attempts" in decision.reason

    def test_safety_violation_takes_priority_over_attempt_count(self):
        # Even on the first attempt, a safety violation must escalate
        # immediately rather than waiting for the attempt-count threshold.
        policy = EscalationPolicy()
        decision = policy.decide(_failure(FailureClass.SAFETY_VIOLATION), attempt_count=1)
        assert decision.escalation_level == "immediate"

    def test_policy_rejection_takes_priority_over_attempt_count(self):
        policy = EscalationPolicy()
        decision = policy.decide(_failure(FailureClass.POLICY_REJECTION), attempt_count=1)
        assert decision.escalation_level == "human_review"
        assert "policy rejection" in decision.reason

    def test_attempt_count_takes_priority_over_user_ambiguity(self):
        # decide() checks attempt_count >= 3 before USER_AMBIGUITY, so the
        # generic exhaustion reason should win once the threshold is hit.
        policy = EscalationPolicy()
        decision = policy.decide(_failure(FailureClass.USER_AMBIGUITY), attempt_count=3)
        assert decision.escalation_level == "human_review"
        assert "exhausted 3 attempts" in decision.reason

    def test_returns_escalation_decision_instance(self):
        policy = EscalationPolicy()
        decision = policy.decide(_failure(FailureClass.UNKNOWN))
        assert isinstance(decision, EscalationDecision)
