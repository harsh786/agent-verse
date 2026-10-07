"""A single-question KB-lookup + arithmetic goal runs on ONE agent, not a fan-out.

Live finding (ONPREM-REASONING, tests/real_world/test_onprem_model_registry_e2e.py):
the goal "compute the free pallet positions at WH-Hosur and at WH-Pune ... say
which warehouse has more free positions and cite the knowledge-base document"
was parked as a supervisor fan-out (``fanout_parent_parked kind=supervisor``).
The fast classifier called it COMPLEX + multi-step (so the multi-agent rule chose
``supervisor``) for two reasons, both measured here:

* every bare "and" counted as a new step — "at WH-Hosur **and** at WH-Pune" (two
  entities of one question) and "... **and** cite the document" (how to present
  the one answer) made step_count 3 → COMPLEX;
* ``requires_web`` matched "now" as a substring of "k*now*ledge", so a
  knowledge-base question was flagged as needing the live web (REALTIME).
"""

from __future__ import annotations

import pytest

from app.orchestration.goal_classifier import GoalClassifier
from app.orchestration.pattern_selector import PatternSelector
from app.orchestration.runtime_profile import Complexity, TimeSensitivity
from app.orchestration.strategy_registry import build_default_registry

E2E_GOAL = (
    "Using only the knowledge base, compute the free pallet positions at WH-Hosur and at "
    "WH-Pune at the end of October (free = rated capacity - (occupied on 1 October + "
    "inbound - outbound)). Show the arithmetic, say which warehouse has more free positions "
    "and cite the knowledge-base document you used."
)
SIMPLE_GOALS = (
    E2E_GOAL,
    "Which site has more free slots, Hosur or Pune?",
    "Which site has more free slots, WH-Hosur and WH-Pune? Show the arithmetic and cite "
    "the document.",
)


@pytest.fixture
def selector() -> PatternSelector:
    return PatternSelector(registry=build_default_registry())


@pytest.mark.parametrize("goal", SIMPLE_GOALS)
def test_single_question_goal_is_not_complex(goal: str) -> None:
    props = GoalClassifier().classify_fast(goal)
    assert props.complexity not in (Complexity.COMPLEX, Complexity.EXPERT), props


@pytest.mark.parametrize("goal", SIMPLE_GOALS)
def test_single_question_goal_runs_on_a_single_agent(goal: str, selector) -> None:
    props = GoalClassifier().classify_fast(goal)
    patterns, _reasons = selector.select_multi_agent(props)
    assert patterns == ["single_agent"], (patterns, props)


def test_knowledge_base_is_not_a_live_web_signal() -> None:
    props = GoalClassifier().classify_fast(E2E_GOAL)
    assert props.requires_web is False
    assert props.time_sensitivity == TimeSensitivity.NORMAL


def test_real_web_signals_still_count() -> None:
    for goal in ("what is the price of gold right now", "get the latest news",
                 "show real-time stock quotes"):
        assert GoalClassifier().classify_fast(goal).requires_web is True, goal


def test_genuinely_multi_step_goals_still_decompose(selector) -> None:
    """Sequenced actions still count, so real multi-step work keeps its fan-out."""
    clf = GoalClassifier()
    goal = ("fetch the incident list then group it by service, after that open a ticket "
            "for each outage")
    props = clf.classify_fast(goal)
    assert props.complexity in (Complexity.COMPLEX, Complexity.EXPERT)
    patterns, _ = selector.select_multi_agent(props)
    assert "supervisor" in patterns or "goal_tree" in patterns


def test_step_boundaries() -> None:
    from app.orchestration.goal_classifier import _count_steps

    assert _count_steps("fetch the file and then email it") == 2  # one boundary
    assert _count_steps("fetch the file and email it") == 2  # "and" + an action
    assert _count_steps("compare WH-Hosur and WH-Pune") == 2  # entity join: no POS tagger
    assert _count_steps("free slots at WH-Hosur and at WH-Pune") == 1
    assert _count_steps("say which is bigger and cite the document") == 1
