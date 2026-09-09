"""WS-2c: org analytics must be computed from real data, not hardcoded constants.

These tests pin the pure computation helpers that replace the 7 fabricated
constants in ``app/org/analytics.py`` (cost_efficiency=0.85, quality_avg=0.82,
escalation_rate=0.05, knowledge_freshness=0.90, security_compliance=0.98, plus
the zero cost-breakdown / model-performance stubs). The DB integration is
exercised by the e2e; here we lock the honest-empty vs computed behaviour.
"""

from __future__ import annotations

from app.org.analytics import (
    OrgHealthScore,
    compute_cost_efficiency,
    compute_escalation_rate,
    compute_quality_avg,
    compute_security_compliance,
)


def test_cost_efficiency_indeterminate_when_no_actual_cost() -> None:
    # No actual spend recorded → honest indeterminate, never a fabricated 0.85.
    assert compute_cost_efficiency(estimated_usd=0.0, actual_usd=0.0) is None
    assert compute_cost_efficiency(estimated_usd=10.0, actual_usd=0.0) is None


def test_cost_efficiency_under_budget_is_high() -> None:
    # Spent less than estimated → efficient (capped at 1.0).
    assert compute_cost_efficiency(estimated_usd=10.0, actual_usd=5.0) == 1.0
    # Spent double the estimate → 0.5 efficiency.
    assert compute_cost_efficiency(estimated_usd=5.0, actual_usd=10.0) == 0.5


def test_quality_avg_from_task_outcomes() -> None:
    assert compute_quality_avg(completed=0, failed=0) is None  # no data → indeterminate
    assert compute_quality_avg(completed=8, failed=2) == 0.8
    assert compute_quality_avg(completed=0, failed=5) == 0.0


def test_escalation_rate_from_events() -> None:
    assert compute_escalation_rate(escalations=0, total_tasks=0) == 0.0
    assert compute_escalation_rate(escalations=1, total_tasks=10) == 0.1
    # Capped at 1.0 even if escalations exceed task count.
    assert compute_escalation_rate(escalations=20, total_tasks=10) == 1.0


def test_security_compliance_from_violations() -> None:
    # No activity → compliant (no violations possible).
    assert compute_security_compliance(violations=0, total_tasks=0) == 1.0
    # One violation in ten tasks → 0.9 compliant.
    assert compute_security_compliance(violations=1, total_tasks=10) == 0.9
    assert compute_security_compliance(violations=100, total_tasks=10) == 0.0


def test_health_score_records_indeterminate_factors() -> None:
    hs = OrgHealthScore(indeterminate_factors={"cost_efficiency", "quality_avg"})
    d = hs.to_dict()
    assert set(d["indeterminate"]) == {"cost_efficiency", "quality_avg"}


def test_no_fabricated_constants_left_in_source() -> None:
    # Guard against the specific fabricated literals returning.
    import inspect

    import app.org.analytics as analytics_mod

    src = inspect.getsource(analytics_mod.OrgAnalyticsService.get_org_health_score)
    for fabricated in ("0.85", "0.82", "0.05", "0.90", "0.98"):
        assert fabricated not in src, f"fabricated constant {fabricated} still hardcoded"
