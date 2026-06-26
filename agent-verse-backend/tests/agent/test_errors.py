"""Tests for ErrorClass and classify_error."""
from __future__ import annotations

import pytest

from app.agent.errors import ErrorClass, classify_error


def test_classify_timeout() -> None:
    assert classify_error(Exception("connection timeout")) == ErrorClass.TOOL_TIMEOUT


def test_classify_budget_exceeded() -> None:
    assert classify_error(Exception("budget exceeded for this tenant")) == ErrorClass.BUDGET_EXCEEDED


def test_classify_permission_denied() -> None:
    assert classify_error(Exception("permission denied: write not allowed")) == ErrorClass.PERMISSION_DENIED


def test_classify_circuit_breaker_open() -> None:
    assert classify_error(Exception("circuit breaker open")) == ErrorClass.CIRCUIT_OPEN


def test_classify_unknown() -> None:
    assert classify_error(Exception("some random unexpected message")) == ErrorClass.UNKNOWN


def test_classify_tool_not_found() -> None:
    assert classify_error(Exception("tool not found: jira.create_issue")) == ErrorClass.TOOL_NOT_FOUND


def test_classify_auth_failed_401() -> None:
    assert classify_error(Exception("request failed with status 401")) == ErrorClass.AUTH_FAILED


def test_classify_guardrail_blocked() -> None:
    assert classify_error(Exception("guardrail triggered: prompt injection detected")) == ErrorClass.GUARDRAIL_BLOCKED


def test_classify_max_iterations() -> None:
    assert classify_error(Exception("max iterations reached")) == ErrorClass.MAX_ITERATIONS


def test_error_class_values_are_strings() -> None:
    """ErrorClass members must be plain strings (StrEnum contract)."""
    assert isinstance(ErrorClass.TOOL_TIMEOUT, str)
    assert ErrorClass.TOOL_TIMEOUT == "tool_timeout"
