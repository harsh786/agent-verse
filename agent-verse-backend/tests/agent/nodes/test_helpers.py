"""Unit tests for app.agent.nodes._helpers — pure utility functions."""
from __future__ import annotations

import pytest

from app.agent.nodes._helpers import (
    _build_verifier_summary,
    _extract_scope_value,
    _extract_tool_name,
    _is_high_risk_step,
    _is_ungrounded_status,
    _parse_json,
    _parse_verifier_response,
)

# ─── _is_high_risk_step ──────────────────────────────────────────────────────


def test_is_high_risk_step_deploy():
    assert _is_high_risk_step("deploy to production server") is True


def test_is_high_risk_step_delete():
    assert _is_high_risk_step("delete all records from table users") is True


def test_is_high_risk_step_rm_command():
    assert _is_high_risk_step("run rm -rf /tmp/data") is True


def test_is_high_risk_step_safe():
    assert _is_high_risk_step("fetch user profile from API") is False


def test_is_high_risk_step_truncate():
    assert _is_high_risk_step("truncate the log file") is True


def test_is_high_risk_step_case_insensitive():
    assert _is_high_risk_step("DEPLOY to staging") is True


# ─── _is_ungrounded_status ───────────────────────────────────────────────────


def test_is_ungrounded_status_true():
    assert _is_ungrounded_status("ungrounded") is True


def test_is_ungrounded_status_false():
    assert _is_ungrounded_status("completed") is False


def test_is_ungrounded_status_enum_like():
    class FakeStatus:
        def __str__(self):
            return "ungrounded"

    assert _is_ungrounded_status(FakeStatus()) is True


# ─── _parse_json ─────────────────────────────────────────────────────────────


def test_parse_json_clean():
    result = _parse_json('{"key": "value", "num": 42}')
    assert result == {"key": "value", "num": 42}


def test_parse_json_markdown_wrapper():
    result = _parse_json('```json\n{"success": true}\n```')
    assert result == {"success": True}


def test_parse_json_plain_text_fallback():
    result = _parse_json("not json at all")
    assert "success" in result or "reason" in result


def test_parse_json_steps_key_fallback():
    result = _parse_json("plain text step", key="steps")
    assert result == {"steps": ["plain text step"]}


def test_parse_json_markdown_no_language():
    result = _parse_json('```\n{"a": 1}\n```')
    assert result == {"a": 1}


# ─── _parse_verifier_response ────────────────────────────────────────────────


def test_parse_verifier_response_json_success():
    result = _parse_verifier_response('{"success": true, "reason": "done"}')
    assert result["success"] is True
    assert result["reason"] == "done"


def test_parse_verifier_response_json_failure():
    result = _parse_verifier_response('{"success": false, "reason": "missing output", "retry": true}')
    assert result["success"] is False
    assert result["retry"] is True


def test_parse_verifier_response_legacy_success():
    result = _parse_verifier_response("SUCCESS: all steps completed")
    assert result["success"] is True
    assert "all steps completed" in result["reason"]


def test_parse_verifier_response_legacy_retry():
    result = _parse_verifier_response("RETRY: needs more data")
    assert result["success"] is False
    assert result["retry"] is True


def test_parse_verifier_response_legacy_fail():
    result = _parse_verifier_response("FAIL: critical error")
    assert result["success"] is False
    assert result["retry"] is False


def test_parse_verifier_response_unknown_positive():
    result = _parse_verifier_response("task completed successfully")
    assert result["success"] is True


def test_parse_verifier_response_unknown_negative():
    result = _parse_verifier_response("there was an error in the execution")
    assert result["success"] is False


def test_parse_verifier_response_json_in_markdown():
    result = _parse_verifier_response('```json\n{"success": false}\n```')
    assert result["success"] is False


# ─── _extract_tool_name ──────────────────────────────────────────────────────


def test_extract_tool_name_with_call():
    assert _extract_tool_name("call github.create_pr with params") == "github.create_pr"


def test_extract_tool_name_no_call():
    assert _extract_tool_name("fetch latest commits") == "llm_call"


def test_extract_tool_name_call_keyword():
    result = _extract_tool_name("make an API call slack.post_message")
    assert result == "slack.post_message"


def test_extract_tool_name_empty():
    assert _extract_tool_name("") == "llm_call"


# ─── _extract_scope_value ────────────────────────────────────────────────────


def test_extract_scope_value_github_repo():
    result = _extract_scope_value("create PR in acme/my-repo for feature branch")
    assert result == "acme/my-repo"


def test_extract_scope_value_jira_key():
    result = _extract_scope_value("resolve issue PROJ-123 by patching the auth module")
    assert result == "PROJ"


def test_extract_scope_value_none():
    result = _extract_scope_value("send a slack message to the team")
    assert result is None


def test_extract_scope_value_prefers_github():
    result = _extract_scope_value("fix PROJ-456 in org/repo")
    assert result == "org/repo"


# ─── _build_verifier_summary ─────────────────────────────────────────────────


class FakeStep:
    def __init__(self, description, output, error=None, tool_calls=None, status=None):
        self.description = description
        self.output = output
        self.error = error
        self.tool_calls = tool_calls or []
        self.status = status


def test_build_verifier_summary_no_steps():
    result = _build_verifier_summary([])
    assert result == "(no steps executed)"


def test_build_verifier_summary_success_steps():
    steps = [FakeStep("fetch user", "user_data = {...}") for _ in range(3)]
    result = _build_verifier_summary(steps)
    assert "MOST RECENT STEPS" in result
    assert "fetch user" in result


def test_build_verifier_summary_failed_tool_call():
    steps = [
        FakeStep(
            "call payment API",
            "error",
            tool_calls=[{"tool_name": "payment.charge", "success": False, "error": "card declined"}],
        )
    ]
    result = _build_verifier_summary(steps)
    assert "[TOOL FAILED]" in result
    assert "card declined" in result


def test_build_verifier_summary_step_error():
    steps = [FakeStep("process data", "", error="TimeoutError after 30s")]
    result = _build_verifier_summary(steps)
    assert "[STEP ERROR]" in result
    assert "TimeoutError" in result


def test_build_verifier_summary_early_failures_shown():
    # 8 steps: first one fails, next 6 succeed, last 1 also fails
    steps = [FakeStep("step-1", "output", error="first error")]
    steps += [FakeStep(f"step-{i}", "ok") for i in range(2, 8)]
    steps.append(FakeStep("step-8", "output", error="last error"))

    result = _build_verifier_summary(steps)
    assert "FAILED STEPS" in result
    assert "first error" in result
    assert "step-1" in result
