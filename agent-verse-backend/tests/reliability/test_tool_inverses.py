"""Tests for tool inverse registry."""
from __future__ import annotations

import pytest

from app.reliability.tool_inverses import (
    _INVERSE_REGISTRY,
    get_inverse_fn,
    register_inverse,
)


def test_get_inverse_fn_unknown_tool_returns_noop():
    fn = get_inverse_fn("unknown:tool", {})
    result = fn()  # should not raise
    assert result is None


def test_get_inverse_fn_known_tool_captures_args():
    captured = {}
    register_inverse("test:tool", lambda args: captured.update(args))
    fn = get_inverse_fn("test:tool", {"key": "value"})
    fn()
    assert captured == {"key": "value"}


def test_get_inverse_fn_args_are_snapshot():
    """Args are captured at registration time, not reference."""
    args = {"a": 1}
    fn = get_inverse_fn("test:snap", args)
    args["a"] = 999  # mutate after capture

    captured = {}
    register_inverse("test:snap", lambda a: captured.update(a))
    fn2 = get_inverse_fn("test:snap", {"a": 1})
    args["a"] = 999
    fn2()
    assert captured.get("a") == 1  # captured original, not mutation


def test_jira_inverse_registered():
    assert "jira:create_issue" in _INVERSE_REGISTRY
    assert "jira_create_issue" in _INVERSE_REGISTRY


def test_builtin_inverses_do_not_raise():
    for tool in ["jira:create_issue", "confluence:create_page", "slack:send_message"]:
        fn = get_inverse_fn(tool, {"issue_id": "TEST-1"})
        fn()  # should not raise


def test_register_inverse_overrides_existing():
    side_effects = []
    register_inverse("override:test", lambda a: side_effects.append("v1"))
    register_inverse("override:test", lambda a: side_effects.append("v2"))
    fn = get_inverse_fn("override:test", {})
    fn()
    assert side_effects == ["v2"]
