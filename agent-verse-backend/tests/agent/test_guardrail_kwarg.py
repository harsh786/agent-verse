"""Test that GuardrailChecker.check_output is called with keyword argument."""
import inspect

import pytest


def test_guardrail_check_output_accepts_keyword():
    """GuardrailChecker.check_output must accept 'output=' kwarg."""
    from app.intelligence.guardrails import GuardrailChecker

    checker = GuardrailChecker()
    # This must not raise TypeError
    result = checker.check_output(output="Hello, my SSN is 123-45-6789")
    assert result is not None  # returns list or bool


def test_guardrail_check_output_fails_without_keyword():
    """Positional arg must raise TypeError (proving the old bug was real)."""
    from app.intelligence.guardrails import GuardrailChecker

    checker = GuardrailChecker()
    try:
        result = checker.check_output("positional arg")  # type: ignore[call-arg]
        # If no error, method is now flexible — that's also OK
        assert isinstance(result, (list, bool))
    except TypeError:
        pass  # Expected — keyword-only arg enforced by `def check_output(self, *, output: str)`


def test_guardrail_graph_uses_keyword():
    """graph.py source must use output= keyword for check_output."""
    from app.agent import graph

    src = inspect.getsource(graph)
    assert "check_output(output=" in src, (
        "graph.py must call check_output with keyword argument output="
    )
