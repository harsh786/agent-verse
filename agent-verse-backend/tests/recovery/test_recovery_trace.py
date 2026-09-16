"""Tests for app.recovery.recovery_trace — observability trace for recovery decisions."""
from __future__ import annotations

from app.recovery.failure_classifier import FailureClass
from app.recovery.recovery_policy import RecoveryAction
from app.recovery.recovery_trace import RecoveryTrace, RecoveryTraceEntry


def test_recovery_trace_entry_defaults_reason_empty() -> None:
    entry = RecoveryTraceEntry(
        failure_class=FailureClass.TIMEOUT, action_taken=RecoveryAction.WAIT_AND_RETRY, attempt=1
    )
    assert entry.reason == ""


def test_recovery_trace_init_sets_goal_id_trace_id_and_empty_entries() -> None:
    trace = RecoveryTrace(goal_id="g-1")
    assert trace.goal_id == "g-1"
    assert isinstance(trace.trace_id, str)
    assert len(trace.trace_id) == 32
    assert trace.entries == []


def test_recovery_trace_trace_id_unique_per_instance() -> None:
    trace_a = RecoveryTrace(goal_id="g-1")
    trace_b = RecoveryTrace(goal_id="g-1")
    assert trace_a.trace_id != trace_b.trace_id


def test_record_appends_entry() -> None:
    trace = RecoveryTrace(goal_id="g-1")
    trace.record(FailureClass.RATE_LIMIT, RecoveryAction.WAIT_AND_RETRY, attempt=1)
    assert len(trace.entries) == 1
    entry = trace.entries[0]
    assert entry.failure_class == FailureClass.RATE_LIMIT
    assert entry.action_taken == RecoveryAction.WAIT_AND_RETRY
    assert entry.attempt == 1
    assert entry.reason == ""


def test_record_with_reason() -> None:
    trace = RecoveryTrace(goal_id="g-1")
    trace.record(
        FailureClass.SAFETY_VIOLATION, RecoveryAction.ABORT, attempt=2, reason="guardrail tripped"
    )
    entry = trace.entries[0]
    assert entry.reason == "guardrail tripped"


def test_record_multiple_entries_accumulate() -> None:
    trace = RecoveryTrace(goal_id="g-1")
    trace.record(FailureClass.RATE_LIMIT, RecoveryAction.WAIT_AND_RETRY, attempt=1)
    trace.record(FailureClass.TOOL_UNAVAILABLE, RecoveryAction.SWITCH_TOOL, attempt=2)
    assert len(trace.entries) == 2


def test_to_dict_structure() -> None:
    trace = RecoveryTrace(goal_id="g-99")
    trace.record(FailureClass.TIMEOUT, RecoveryAction.WAIT_AND_RETRY, attempt=1, reason="slow")
    trace.record(
        FailureClass.UNKNOWN, RecoveryAction.ESCALATE_TO_HUMAN, attempt=2, reason="no match"
    )

    data = trace.to_dict()
    assert data["goal_id"] == "g-99"
    assert data["trace_id"] == trace.trace_id
    assert data["total_attempts"] == 2
    assert data["entries"] == [
        {
            "failure_class": "timeout",
            "action": "wait_and_retry",
            "attempt": 1,
            "reason": "slow",
        },
        {
            "failure_class": "unknown",
            "action": "escalate_to_human",
            "attempt": 2,
            "reason": "no match",
        },
    ]


def test_to_dict_empty_trace() -> None:
    trace = RecoveryTrace(goal_id="g-empty")
    data = trace.to_dict()
    assert data["total_attempts"] == 0
    assert data["entries"] == []
