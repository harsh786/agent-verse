"""Tests for FailureClassifier + RecoveryPolicy + RetryStrategySelector — 12 tests."""
from __future__ import annotations

from app.recovery.failure_classifier import FailureClass, FailureClassifier
from app.recovery.recovery_policy import RecoveryAction, RecoveryPolicy
from app.recovery.retry_strategy_selector import RetryStrategySelector

# ---------------------------------------------------------------------------
# FailureClassifier — 6 tests
# ---------------------------------------------------------------------------

def test_classifier_detects_auth_failure() -> None:
    clf = FailureClassifier()
    result = clf.classify("HTTP 401 unauthorized request")
    assert result.failure_class == FailureClass.AUTH_FAILURE
    assert result.confidence == 0.9


def test_classifier_detects_rate_limit() -> None:
    clf = FailureClassifier()
    result = clf.classify("429 Too Many Requests — rate limit exceeded")
    assert result.failure_class == FailureClass.RATE_LIMIT


def test_classifier_detects_timeout() -> None:
    clf = FailureClassifier()
    result = clf.classify("Operation timed out after 30s")
    assert result.failure_class == FailureClass.TIMEOUT


def test_classifier_detects_safety_violation() -> None:
    clf = FailureClassifier()
    result = clf.classify("Blocked by guardrail: injection detected")
    assert result.failure_class == FailureClass.SAFETY_VIOLATION


def test_classifier_detects_context_gap() -> None:
    clf = FailureClassifier()
    result = clf.classify("Insufficient data — cannot determine the answer")
    assert result.failure_class == FailureClass.CONTEXT_GAP


def test_classifier_unknown_returns_unknown() -> None:
    clf = FailureClassifier()
    result = clf.classify("Something weird happened unexpectedly")
    assert result.failure_class == FailureClass.UNKNOWN
    assert result.confidence == 0.5


# ---------------------------------------------------------------------------
# RecoveryPolicy — 4 tests
# ---------------------------------------------------------------------------

def test_policy_rate_limit_wait_and_retry() -> None:
    clf = FailureClassifier()
    policy = RecoveryPolicy()
    failure = clf.classify("429 rate limit hit")
    action = policy.select(failure)
    assert action == RecoveryAction.WAIT_AND_RETRY


def test_policy_safety_violation_abort() -> None:
    clf = FailureClassifier()
    policy = RecoveryPolicy()
    failure = clf.classify("safety violation detected by guardrail")
    action = policy.select(failure)
    assert action == RecoveryAction.ABORT


def test_policy_tool_unavailable_switch_tool() -> None:
    clf = FailureClassifier()
    policy = RecoveryPolicy()
    failure = clf.classify("connection error — tool not responding")
    action = policy.select(failure)
    assert action == RecoveryAction.SWITCH_TOOL


def test_policy_unknown_escalates_to_human() -> None:
    clf = FailureClassifier()
    policy = RecoveryPolicy()
    failure = clf.classify("some completely novel error nobody expected")
    action = policy.select(failure)
    assert action == RecoveryAction.ESCALATE_TO_HUMAN


# ---------------------------------------------------------------------------
# RetryStrategySelector — 2 tests
# ---------------------------------------------------------------------------

def test_retry_selector_rate_limit_has_backoff() -> None:
    selector = RetryStrategySelector()
    strategy = selector.select(FailureClass.RATE_LIMIT)
    assert strategy.max_retries == 3
    assert strategy.backoff_factor == 2.0
    assert strategy.initial_delay_seconds == 5.0


def test_retry_selector_unknown_returns_default() -> None:
    selector = RetryStrategySelector()
    strategy = selector.select(FailureClass.UNKNOWN)
    assert strategy.max_retries == 1
