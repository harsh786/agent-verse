from __future__ import annotations

import enum
import re
from dataclasses import dataclass


class FailureClass(str, enum.Enum):
    AUTH_FAILURE = "auth_failure"
    MISSING_CREDENTIAL = "missing_credential"
    TOOL_UNAVAILABLE = "tool_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CONTEXT_GAP = "context_gap"
    POLICY_REJECTION = "policy_rejection"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CODE_TEST_FAILURE = "code_test_failure"
    USER_AMBIGUITY = "user_ambiguity"
    SAFETY_VIOLATION = "safety_violation"
    UNKNOWN = "unknown"


@dataclass
class FailureResult:
    failure_class: FailureClass
    error_text: str
    confidence: float = 0.7


_PATTERNS: list[tuple[FailureClass, re.Pattern[str]]] = [
    (
        FailureClass.AUTH_FAILURE,
        re.compile(r"(?i)(401|unauthorized|invalid api key|authentication failed)"),
    ),
    (FailureClass.RATE_LIMIT, re.compile(r"(?i)(429|rate limit|too many requests|throttl)")),
    (FailureClass.TIMEOUT, re.compile(r"(?i)(timeout|timed out|deadline exceeded)")),
    (
        FailureClass.TOOL_UNAVAILABLE,
        re.compile(r"(?i)(connection error|tool.*not.*respond|service unavailable|503)"),
    ),
    (
        FailureClass.PROVIDER_UNAVAILABLE,
        re.compile(r"(?i)(provider.*unavailable|llm.*down|openai.*error|anthropic.*error)"),
    ),
    (
        FailureClass.CONTEXT_GAP,
        re.compile(
            r"(?i)(insufficient data|cannot determine|lack of context|not found|more context)"
        ),
    ),
    (
        FailureClass.POLICY_REJECTION,
        re.compile(r"(?i)(policy.*denied|policyengine|denied by policy|not allowed)"),
    ),
    (
        FailureClass.SAFETY_VIOLATION,
        re.compile(r"(?i)(guardrail|injection detected|safety violation|blocked by)"),
    ),
    (
        FailureClass.MISSING_CREDENTIAL,
        re.compile(r"(?i)(missing.*credential|no.*api key|credential not found)"),
    ),
    (
        FailureClass.USER_AMBIGUITY,
        re.compile(r"(?i)(ambiguous|requires clarification|unclear goal|do the thing)"),
    ),
    (
        FailureClass.CODE_TEST_FAILURE,
        re.compile(r"(?i)(test.*fail|assertion error|syntax error|compilation error)"),
    ),
]


class FailureClassifier:
    def classify(self, error_text: str) -> FailureResult:
        for failure_class, pattern in _PATTERNS:
            if pattern.search(error_text):
                return FailureResult(
                    failure_class=failure_class,
                    error_text=error_text,
                    confidence=0.9,
                )
        return FailureResult(
            failure_class=FailureClass.UNKNOWN,
            error_text=error_text,
            confidence=0.5,
        )
