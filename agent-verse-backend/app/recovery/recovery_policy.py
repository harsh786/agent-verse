from __future__ import annotations

import enum

from app.recovery.failure_classifier import FailureClass, FailureResult


class RecoveryAction(enum.StrEnum):
    WAIT_AND_RETRY = "wait_and_retry"
    SWITCH_TOOL = "switch_tool"
    FETCH_MORE_CONTEXT = "fetch_more_context"
    REQUEST_CREDENTIALS = "request_credentials"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    DECOMPOSE_GOAL = "decompose_goal"
    ABORT = "abort"


_POLICY: dict[FailureClass, RecoveryAction] = {
    FailureClass.AUTH_FAILURE: RecoveryAction.REQUEST_CREDENTIALS,
    FailureClass.MISSING_CREDENTIAL: RecoveryAction.REQUEST_CREDENTIALS,
    FailureClass.RATE_LIMIT: RecoveryAction.WAIT_AND_RETRY,
    FailureClass.TIMEOUT: RecoveryAction.WAIT_AND_RETRY,
    FailureClass.TOOL_UNAVAILABLE: RecoveryAction.SWITCH_TOOL,
    FailureClass.PROVIDER_UNAVAILABLE: RecoveryAction.SWITCH_TOOL,
    FailureClass.CONTEXT_GAP: RecoveryAction.FETCH_MORE_CONTEXT,
    FailureClass.POLICY_REJECTION: RecoveryAction.ESCALATE_TO_HUMAN,
    FailureClass.SAFETY_VIOLATION: RecoveryAction.ABORT,
    FailureClass.USER_AMBIGUITY: RecoveryAction.ESCALATE_TO_HUMAN,
    FailureClass.CODE_TEST_FAILURE: RecoveryAction.DECOMPOSE_GOAL,
    FailureClass.UNKNOWN: RecoveryAction.ESCALATE_TO_HUMAN,
}


class RecoveryPolicy:
    def select(self, failure: FailureResult) -> RecoveryAction:
        return _POLICY.get(failure.failure_class, RecoveryAction.ESCALATE_TO_HUMAN)
