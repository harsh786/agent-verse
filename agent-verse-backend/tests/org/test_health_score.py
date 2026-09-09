"""Tests for OrgHealthScore — app/org/analytics.py"""
from __future__ import annotations

from app.org.analytics import OrgHealthScore


def test_health_score_perfect_org():
    hs = OrgHealthScore(
        mission_completion_rate=1.0,
        agent_utilization=1.0,
        cost_efficiency=1.0,
        quality_avg=1.0,
        blocked_ratio=0.0,
        escalation_rate=0.0,
        knowledge_freshness=1.0,
        security_compliance=1.0,
    )
    assert hs.score == 100.0


def test_health_score_empty_org():
    hs = OrgHealthScore()
    # Default: some neutral values
    assert 0.0 <= hs.score <= 100.0


def test_health_score_penalises_blocked_ratio():
    hs_no_block = OrgHealthScore(blocked_ratio=0.0, mission_completion_rate=0.8)
    hs_blocked  = OrgHealthScore(blocked_ratio=1.0, mission_completion_rate=0.8)
    assert hs_no_block.score > hs_blocked.score


def test_health_score_penalises_escalation():
    hs_low  = OrgHealthScore(escalation_rate=0.0)
    hs_high = OrgHealthScore(escalation_rate=1.0)
    assert hs_low.score > hs_high.score


def test_health_score_never_negative():
    hs = OrgHealthScore(
        mission_completion_rate=0.0,
        blocked_ratio=1.0,
        escalation_rate=1.0,
        quality_avg=0.0,
    )
    assert hs.score >= 0.0


def test_health_score_never_exceeds_100():
    hs = OrgHealthScore(
        mission_completion_rate=2.0,  # out-of-range inputs clamped
        quality_avg=2.0,
    )
    assert hs.score <= 100.0


def test_health_score_to_dict_has_factors():
    hs = OrgHealthScore(mission_completion_rate=0.7, quality_avg=0.8)
    d  = hs.to_dict()
    assert "score" in d
    assert "factors" in d
    assert "mission_completion_rate" in d["factors"]
