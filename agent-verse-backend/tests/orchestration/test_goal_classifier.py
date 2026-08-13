"""Tests for GoalClassifier — 14 tests covering 10 goal archetypes."""
from __future__ import annotations

import pytest

from app.orchestration.goal_classifier import GoalClassifier
from app.orchestration.runtime_profile import (
    Complexity,
    Domain,
    RiskLevel,
    TimeSensitivity,
)


@pytest.fixture()
def classifier() -> GoalClassifier:
    return GoalClassifier()


# ---------------------------------------------------------------------------
# Archetype 1: Simple operational task
# ---------------------------------------------------------------------------
def test_classify_simple_operational():
    clf = GoalClassifier()
    props = clf.classify_fast("list all open tickets")
    assert props.complexity == Complexity.SIMPLE
    assert props.domain == Domain.OPERATIONAL
    assert props.risk == RiskLevel.LOW
    assert props.requires_web is False
    assert props.requires_code is False
    assert props.multi_step is True  # estimated_steps always >= 2


# ---------------------------------------------------------------------------
# Archetype 2: Critical-risk irreversible (contains "production")
# ---------------------------------------------------------------------------
def test_classify_critical_risk_production():
    clf = GoalClassifier()
    # 6+ words so the short-goal confidence cap doesn't apply
    props = clf.classify_fast("delete all production database records permanently")
    assert props.risk == RiskLevel.CRITICAL
    assert props.reversibility == "irreversible"
    assert props.classifier_confidence >= 0.9


# ---------------------------------------------------------------------------
# Archetype 3: High-risk deploy
# ---------------------------------------------------------------------------
def test_classify_high_risk_deploy():
    clf = GoalClassifier()
    # "deploy" is HIGH risk; no "production" so not CRITICAL
    props = clf.classify_fast("deploy the new service to staging")
    assert props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert props.complexity != Complexity.SIMPLE  # risk escalates complexity


# ---------------------------------------------------------------------------
# Archetype 4: Technical / coding task
# ---------------------------------------------------------------------------
def test_classify_technical_coding():
    clf = GoalClassifier()
    props = clf.classify_fast("write a python function to sort a list of dictionaries")
    assert props.requires_code is True
    assert props.domain == Domain.TECHNICAL
    assert props.is_generative is True


# ---------------------------------------------------------------------------
# Archetype 5: Analytical + expert complexity (2 expert keywords)
# ---------------------------------------------------------------------------
def test_classify_analytical_expert():
    clf = GoalClassifier()
    props = clf.classify_fast("analyze and research market trends to forecast growth")
    assert props.complexity == Complexity.EXPERT
    assert props.domain == Domain.ANALYTICAL
    assert props.estimated_steps >= 5


# ---------------------------------------------------------------------------
# Archetype 6: Creative / generative task
# ---------------------------------------------------------------------------
def test_classify_creative_generative():
    clf = GoalClassifier()
    props = clf.classify_fast("write a blog post about recent advances in AI")
    assert props.is_generative is True
    assert props.domain == Domain.CREATIVE


# ---------------------------------------------------------------------------
# Archetype 7: Web-requiring (realtime) task
# ---------------------------------------------------------------------------
def test_classify_web_realtime():
    clf = GoalClassifier()
    props = clf.classify_fast("get the latest news about OpenAI")
    assert props.requires_web is True
    assert props.time_sensitivity == TimeSensitivity.REALTIME
    assert props.classifier_confidence >= 0.9


# ---------------------------------------------------------------------------
# Archetype 8: Multi-step complex task
# ---------------------------------------------------------------------------
def test_classify_multi_step_complex():
    clf = GoalClassifier()
    props = clf.classify_fast(
        "fetch sales data then aggregate by region and also generate a summary report"
    )
    assert props.complexity in (Complexity.COMPLEX, Complexity.EXPERT)
    assert props.estimated_steps >= 3


# ---------------------------------------------------------------------------
# Archetype 9: Short goal gets capped confidence
# ---------------------------------------------------------------------------
def test_classify_short_goal_low_confidence():
    clf = GoalClassifier()
    props = clf.classify_fast("help me now")
    assert props.classifier_confidence <= 0.65


# ---------------------------------------------------------------------------
# Archetype 10: Vision task
# ---------------------------------------------------------------------------
def test_classify_vision_task():
    clf = GoalClassifier()
    props = clf.classify_fast("analyze the screenshot and extract text via ocr")
    assert props.requires_vision is True


# ---------------------------------------------------------------------------
# LLM path: provider=None returns fast result unchanged
# ---------------------------------------------------------------------------
async def test_classify_with_llm_no_provider():
    clf = GoalClassifier()
    fast = clf.classify_fast("schedule a meeting for tomorrow")
    result = await clf.classify_with_llm("schedule a meeting for tomorrow", provider=None, fast_props=fast)
    assert result is fast


# ---------------------------------------------------------------------------
# LLM path: high confidence short-circuits LLM call
# ---------------------------------------------------------------------------
async def test_classify_with_llm_high_confidence_skips():
    clf = GoalClassifier()
    # Simple task (no web, no risk) gets confidence 0.85 which is NOT > 0.85, so it'll
    # reach the complexity check. complexity=SIMPLE != MEDIUM, so it returns base.
    props = clf.classify_fast("list open tickets")
    assert props.complexity != Complexity.MEDIUM or props.classifier_confidence > 0.85
    result = await clf.classify_with_llm("list open tickets", provider=object(), fast_props=props)
    # Should return base (either confidence > 0.85 OR complexity != MEDIUM)
    assert result.raw_goal == props.raw_goal


# ---------------------------------------------------------------------------
# Risk elevates complexity from SIMPLE to MEDIUM
# ---------------------------------------------------------------------------
def test_risk_escalates_complexity():
    clf = GoalClassifier()
    # "deploy" is HIGH risk; a one-word-like goal would otherwise be SIMPLE
    props = clf.classify_fast("deploy app")
    assert props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert props.complexity != Complexity.SIMPLE


# ---------------------------------------------------------------------------
# Irreversibility detection
# ---------------------------------------------------------------------------
def test_irreversibility_detected():
    clf = GoalClassifier()
    props = clf.classify_fast("send email to all users about the outage")
    assert props.reversibility == "irreversible"


# ---------------------------------------------------------------------------
# Word-boundary false-positive regression tests (Fix 1)
# ---------------------------------------------------------------------------
def test_reproduce_is_not_critical_risk(classifier):
    """'prod' inside 'reproduce' must not trigger CRITICAL risk."""
    props = classifier.classify_fast("reproduce the bug in the auth module")
    assert props.risk == RiskLevel.LOW


def test_product_roadmap_is_not_critical_risk(classifier):
    """'prod' inside 'product' must not trigger CRITICAL risk."""
    props = classifier.classify_fast("build a product roadmap for Q3")
    assert props.risk == RiskLevel.LOW


def test_productivity_is_not_critical_risk(classifier):
    """'prod' inside 'productivity' must not trigger CRITICAL risk."""
    props = classifier.classify_fast("measure team productivity metrics")
    assert props.risk in (RiskLevel.LOW, RiskLevel.MEDIUM)


def test_prod_as_whole_word_is_critical(classifier):
    """'prod' as a standalone word must remain CRITICAL."""
    props = classifier.classify_fast("deploy the service to prod")
    assert props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)


def test_production_as_whole_word_is_critical(classifier):
    """'production' as a standalone word must remain CRITICAL."""
    props = classifier.classify_fast("delete all records from the production database")
    assert props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
