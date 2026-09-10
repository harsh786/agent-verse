"""Tests for GoalClassifier — two-tier doc-4 exact implementation."""
from __future__ import annotations

import pytest

from app.agent.goal_classifier import GoalClassifier, goal_classifier
from app.agent.pattern_config import Complexity, Domain, RiskLevel


@pytest.fixture
def clf() -> GoalClassifier:
    return GoalClassifier()


def test_simple_list_goal(clf: GoalClassifier) -> None:
    props = clf.classify_fast("list all users in the database")
    assert props.complexity == Complexity.SIMPLE
    assert props.risk == RiskLevel.LOW


def test_expert_architecture_goal(clf: GoalClassifier) -> None:
    props = clf.classify_fast("design and architect a scalable distributed system")
    assert props.complexity == Complexity.EXPERT


def test_high_risk_delete(clf: GoalClassifier) -> None:
    props = clf.classify_fast("delete all production data from the database")
    assert props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert props.reversibility == "irreversible"


def test_web_signals_require_web(clf: GoalClassifier) -> None:
    props = clf.classify_fast("what is the latest version of Python available now")
    assert props.requires_web is True
    assert props.time_sensitivity == "realtime"
    assert props.knowledge_requirement == "web_required"


def test_creative_domain(clf: GoalClassifier) -> None:
    props = clf.classify_fast("write a short poem about the ocean")
    assert props.domain == Domain.CREATIVE
    assert props.is_generative is True


def test_analytical_domain(clf: GoalClassifier) -> None:
    props = clf.classify_fast("analyze and evaluate the performance metrics of the system")
    assert props.domain == Domain.ANALYTICAL


def test_technical_domain(clf: GoalClassifier) -> None:
    props = clf.classify_fast("debug the python code in the api server")
    assert props.domain == Domain.TECHNICAL


def test_operational_domain(clf: GoalClassifier) -> None:
    props = clf.classify_fast("restart the server and monitor the alerts")
    assert props.domain == Domain.OPERATIONAL


def test_payment_irreversible(clf: GoalClassifier) -> None:
    props = clf.classify_fast("process a payment of $100 to the customer")
    assert props.reversibility == "irreversible"
    assert props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)


def test_high_confidence_obvious_goal(clf: GoalClassifier) -> None:
    props = clf.classify_fast("delete all records from the users table immediately")
    assert props.confidence >= 0.9


def test_low_confidence_vague_goal(clf: GoalClassifier) -> None:
    props = clf.classify_fast("do it")
    assert props.confidence <= 0.65


async def test_llm_fallback_on_none_provider(clf: GoalClassifier) -> None:
    fast = clf.classify_fast("implement a caching layer for the API")
    result = await clf.classify_with_llm("implement a caching layer for the API", None, fast)
    assert result is fast


def test_singleton_exists() -> None:
    assert goal_classifier is not None
    assert isinstance(goal_classifier, GoalClassifier)
