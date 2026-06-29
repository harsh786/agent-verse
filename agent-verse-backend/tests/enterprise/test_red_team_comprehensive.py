"""Comprehensive tests for app/enterprise/red_team.py."""
from __future__ import annotations

import pytest

from app.enterprise.red_team import (
    BehavioralRedTeamRunner,
    RedTeamReport,
    RedTeamRunner,
    _ADVERSARIAL_CASES,
)
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="rt-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


# ── _ADVERSARIAL_CASES ────────────────────────────────────────────────────────


def test_adversarial_cases_has_5_entries() -> None:
    assert len(_ADVERSARIAL_CASES) == 5


def test_adversarial_cases_have_required_fields() -> None:
    for case in _ADVERSARIAL_CASES:
        assert "id" in case
        assert "description" in case
        assert "payload" in case


def test_adversarial_cases_have_non_empty_payloads() -> None:
    for case in _ADVERSARIAL_CASES:
        assert case["payload"].strip()


def test_adversarial_case_ids_are_unique() -> None:
    ids = [c["id"] for c in _ADVERSARIAL_CASES]
    assert len(ids) == len(set(ids))


def test_adversarial_case_ids_known() -> None:
    ids = {c["id"] for c in _ADVERSARIAL_CASES}
    expected = {
        "prompt_injection",
        "resource_exhaustion",
        "data_exfiltration",
        "bad_format",
        "guardrail_bypass",
    }
    assert ids == expected


# ── RedTeamReport ─────────────────────────────────────────────────────────────


def test_red_team_report_defaults() -> None:
    report = RedTeamReport()
    assert report.cases_run == 0
    assert report.cases_passed == 0
    assert report.cases_failed == 0
    assert report.results == []
    assert report.report_id


def test_red_team_report_total_property() -> None:
    report = RedTeamReport(cases_run=5)
    assert report.total == 5


def test_red_team_report_passed_property() -> None:
    report = RedTeamReport(cases_passed=3)
    assert report.passed == 3


def test_red_team_report_failed_property() -> None:
    report = RedTeamReport(cases_failed=2)
    assert report.failed == 2


def test_red_team_report_run_at_property() -> None:
    report = RedTeamReport()
    assert report.run_at == report.created_at
    assert "T" in report.run_at  # ISO format includes "T"


def test_red_team_report_unique_ids() -> None:
    r1 = RedTeamReport()
    r2 = RedTeamReport()
    assert r1.report_id != r2.report_id


# ── RedTeamRunner.run ─────────────────────────────────────────────────────────


def test_red_team_runner_run_all_cases() -> None:
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=T)

    assert report.cases_run == 5
    assert report.cases_run == report.cases_passed + report.cases_failed
    assert len(report.results) == 5


def test_red_team_runner_run_filtered_cases() -> None:
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=T, cases=["prompt_injection", "guardrail_bypass"])

    assert report.cases_run == 2
    assert len(report.results) == 2


def test_red_team_runner_run_single_case() -> None:
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=T, cases=["prompt_injection"])

    assert report.cases_run == 1
    assert len(report.results) == 1
    assert report.results[0]["case_id"] == "prompt_injection"


def test_red_team_runner_results_have_required_fields() -> None:
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=T)

    for result in report.results:
        assert "case_id" in result
        assert "name" in result
        assert "status" in result
        assert "details" in result
        assert "detected" in result
        assert "outcome" in result


def test_red_team_runner_results_status_is_passed_or_failed() -> None:
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=T)

    for result in report.results:
        assert result["status"] in {"passed", "failed"}


def test_red_team_runner_results_outcome_is_blocked_or_passed_through() -> None:
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=T)

    for result in report.results:
        assert result["outcome"] in {"blocked", "passed_through"}


def test_red_team_runner_prompt_injection_detected() -> None:
    """Prompt injection should be blocked by guardrails."""
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=T, cases=["prompt_injection"])

    result = report.results[0]
    # Guardrail should detect the injection attempt
    assert result["detected"] is True
    assert result["outcome"] == "blocked"
    assert report.cases_passed == 1
    assert report.cases_failed == 0


def test_red_team_runner_at_least_one_case_blocked() -> None:
    """At minimum, prompt injection must be blocked (regression guard)."""
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=T)
    blocked_count = sum(1 for r in report.results if r["detected"])
    assert blocked_count >= 1


def test_red_team_runner_report_stored() -> None:
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=T)
    assert runner.get_report(report_id=report.report_id, tenant_ctx=T) is report


def test_red_team_runner_get_report_missing_returns_none() -> None:
    runner = RedTeamRunner()
    result = runner.get_report(report_id="ghost-id", tenant_ctx=T)
    assert result is None


def test_red_team_runner_unknown_case_id_filtered_out() -> None:
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=T, cases=["nonexistent_case_id"])
    assert report.cases_run == 0
    assert report.results == []


# ── RedTeamRunner — internal count consistency ────────────────────────────────


def test_red_team_passed_plus_failed_equals_run() -> None:
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=T)
    assert report.cases_passed + report.cases_failed == report.cases_run


def test_red_team_result_detected_matches_status() -> None:
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=T)
    for r in report.results:
        if r["detected"]:
            assert r["status"] == "passed"
            assert r["outcome"] == "blocked"
        else:
            assert r["status"] == "failed"
            assert r["outcome"] == "passed_through"


# ── BehavioralRedTeamRunner._analyze_events ───────────────────────────────────


class TestBehavioralAnalyzeEvents:
    def _runner(self) -> BehavioralRedTeamRunner:
        return BehavioralRedTeamRunner()

    def test_tool_call_denied_returns_true(self) -> None:
        runner = self._runner()
        events = [{"type": "tool_call_denied"}]
        assert runner._analyze_events(events, {}) is True

    def test_goal_cancelled_returns_true(self) -> None:
        runner = self._runner()
        events = [{"type": "goal_cancelled"}]
        assert runner._analyze_events(events, {}) is True

    def test_goal_failed_with_injection_reason_returns_true(self) -> None:
        runner = self._runner()
        events = [{"type": "goal_failed", "reason": "guardrail injection detected"}]
        assert runner._analyze_events(events, {}) is True

    def test_goal_failed_with_denied_reason_returns_true(self) -> None:
        runner = self._runner()
        events = [{"type": "goal_failed", "reason": "tool call denied by policy"}]
        assert runner._analyze_events(events, {}) is True

    def test_goal_failed_with_blocked_reason_returns_true(self) -> None:
        runner = self._runner()
        events = [{"type": "goal_failed", "reason": "goal blocked by safety filter"}]
        assert runner._analyze_events(events, {}) is True

    def test_goal_failed_neutral_reason_returns_false(self) -> None:
        runner = self._runner()
        events = [{"type": "goal_failed", "reason": "LLM timeout"}]
        assert runner._analyze_events(events, {}) is False

    def test_error_event_returns_true(self) -> None:
        runner = self._runner()
        events = [{"type": "error", "reason": "Rejected"}]
        assert runner._analyze_events(events, {}) is True

    def test_empty_events_returns_false(self) -> None:
        runner = self._runner()
        assert runner._analyze_events([], {}) is False

    def test_goal_complete_without_block_returns_false(self) -> None:
        runner = self._runner()
        events = [{"type": "goal_complete", "output": "done"}]
        assert runner._analyze_events(events, {}) is False

    def test_multiple_events_first_denial_wins(self) -> None:
        runner = self._runner()
        events = [
            {"type": "step_started"},
            {"type": "tool_call_denied"},
            {"type": "goal_failed"},
        ]
        assert runner._analyze_events(events, {}) is True

    def test_redacted_in_failure_reason_returns_true(self) -> None:
        runner = self._runner()
        events = [{"type": "goal_failed", "reason": "content redacted by policy"}]
        assert runner._analyze_events(events, {}) is True
