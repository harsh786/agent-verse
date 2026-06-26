"""Structured error classification for agent execution."""
from __future__ import annotations

import enum


class ErrorClass(enum.StrEnum):
    TOOL_TIMEOUT       = "tool_timeout"
    LLM_REFUSAL        = "llm_refusal"
    PERMISSION_DENIED  = "permission_denied"
    BUDGET_EXCEEDED    = "budget_exceeded"
    MAX_ITERATIONS     = "max_iterations"
    GUARDRAIL_BLOCKED  = "guardrail_blocked"
    CIRCUIT_OPEN       = "circuit_open"
    TOOL_NOT_FOUND     = "tool_not_found"
    AUTH_FAILED        = "auth_failed"
    CHECKPOINT_FAILED  = "checkpoint_failed"
    VALIDATION_ERROR   = "validation_error"
    UNKNOWN            = "unknown"


def classify_error(exc: Exception) -> str:
    """Classify an exception into an ErrorClass."""
    msg = str(exc).lower()
    if "timeout" in msg:
        return ErrorClass.TOOL_TIMEOUT
    if "budget" in msg or "cost" in msg or "limit" in msg:
        return ErrorClass.BUDGET_EXCEEDED
    if "permission" in msg or "denied" in msg or "forbidden" in msg:
        return ErrorClass.PERMISSION_DENIED
    if "circuit" in msg or "breaker" in msg:
        return ErrorClass.CIRCUIT_OPEN
    if "not found" in msg and ("tool" in msg or "connector" in msg):
        return ErrorClass.TOOL_NOT_FOUND
    if "guardrail" in msg or "injection" in msg or "redacted" in msg:
        return ErrorClass.GUARDRAIL_BLOCKED
    if "auth" in msg or "401" in msg or "403" in msg or "unauthorized" in msg:
        return ErrorClass.AUTH_FAILED
    if "max_iter" in msg or "max iterations" in msg:
        return ErrorClass.MAX_ITERATIONS
    if "refusal" in msg or "cannot" in msg or "decline" in msg:
        return ErrorClass.LLM_REFUSAL
    return ErrorClass.UNKNOWN
