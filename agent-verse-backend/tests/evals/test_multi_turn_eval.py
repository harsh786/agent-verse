"""Tests for MultiTurnEvaluator — multi-turn dialogue scoring."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.evals.multi_turn_eval import (
    MultiTurnCase,
    MultiTurnEvaluator,
    MultiTurnResult,
    Turn,
    TurnScore,
)


def _case(criteria: list[str] | None = None, expected_final: str = "goal achieved here") -> MultiTurnCase:
    return MultiTurnCase(
        name="case-1",
        turns=[
            Turn(role="user", content="Hello, can you help me?"),
            Turn(role="assistant", content="Sure, what do you need?"),
            Turn(role="user", content="I need the goal achieved"),
        ],
        expected_final=expected_final,
        eval_criteria=criteria or [],
    )


def _fake_provider(content: str = '{"coherence": 8, "relevance": 7}') -> AsyncMock:
    provider = AsyncMock()
    response = AsyncMock()
    response.content = content
    provider.complete = AsyncMock(return_value=response)
    return provider


# ---------------------------------------------------------------------------
# dataclasses
# ---------------------------------------------------------------------------


def test_turn_defaults_metadata_empty_dict() -> None:
    t = Turn(role="user", content="hi")
    assert t.metadata == {}


def test_multi_turn_case_defaults_criteria_empty() -> None:
    case = MultiTurnCase(name="n", turns=[], expected_final="x")
    assert case.eval_criteria == []


# ---------------------------------------------------------------------------
# MultiTurnEvaluator.evaluate — happy path
# ---------------------------------------------------------------------------


async def test_evaluate_runs_all_user_turns_and_scores() -> None:
    provider = _fake_provider()
    evaluator = MultiTurnEvaluator(provider)
    case = _case(criteria=["goal achieved"])

    agent_fn = AsyncMock(return_value="the goal achieved here, done")

    result = await evaluator.evaluate(case, agent_fn)

    assert isinstance(result, MultiTurnResult)
    assert result.case_name == "case-1"
    assert result.turns_completed == 2  # two user turns in the case
    assert result.expected_turns == 2
    assert result.goal_achieved is True
    assert "goal achieved" in result.criteria_met
    assert result.criteria_failed == []
    assert 0.0 <= result.coherence_score <= 1.0
    assert 0.0 <= result.overall_score <= 1.0
    assert len(result.per_turn_scores) == 2
    assert all(isinstance(s, TurnScore) for s in result.per_turn_scores)
    assert agent_fn.await_count == 2


async def test_evaluate_criteria_not_met_recorded_as_failed() -> None:
    provider = _fake_provider()
    evaluator = MultiTurnEvaluator(provider)
    case = _case(criteria=["mentions python", "asks clarifying question"], expected_final="nothing matches")

    agent_fn = AsyncMock(return_value="a totally unrelated reply")

    result = await evaluator.evaluate(case, agent_fn)

    assert result.criteria_met == []
    assert set(result.criteria_failed) == {"mentions python", "asks clarifying question"}
    assert result.goal_achieved is False


async def test_evaluate_agent_fn_exception_yields_zero_score_turn() -> None:
    provider = _fake_provider()
    evaluator = MultiTurnEvaluator(provider)
    case = _case()

    agent_fn = AsyncMock(side_effect=RuntimeError("boom"))

    result = await evaluator.evaluate(case, agent_fn)

    assert result.turns_completed == 0
    assert len(result.per_turn_scores) == 2
    assert all(s.coherence == 0.0 and s.relevance == 0.0 and s.content == "" for s in result.per_turn_scores)
    assert result.coherence_score == 0.0


async def test_evaluate_no_user_turns_returns_zero_coherence() -> None:
    provider = _fake_provider()
    evaluator = MultiTurnEvaluator(provider)
    case = MultiTurnCase(name="empty", turns=[], expected_final="x", eval_criteria=[])

    agent_fn = AsyncMock(return_value="reply")

    result = await evaluator.evaluate(case, agent_fn)

    assert result.turns_completed == 0
    assert result.expected_turns == 0
    assert result.coherence_score == 0.0
    assert result.per_turn_scores == []


# ---------------------------------------------------------------------------
# _score_turn
# ---------------------------------------------------------------------------


async def test_score_turn_parses_json_response() -> None:
    provider = _fake_provider('{"coherence": 10, "relevance": 5}')
    evaluator = MultiTurnEvaluator(provider)
    turn = Turn(role="assistant", content="a reply")

    score = await evaluator._score_turn(history=[], turn=turn, turn_index=0)

    assert score.turn_index == 0
    assert score.coherence == 1.0
    assert score.relevance == 0.5
    assert score.content == "a reply"


async def test_score_turn_falls_back_on_bad_json() -> None:
    provider = _fake_provider("not json at all")
    evaluator = MultiTurnEvaluator(provider)
    turn = Turn(role="assistant", content="a reply")

    score = await evaluator._score_turn(history=[], turn=turn, turn_index=2)

    assert score.turn_index == 2
    assert score.coherence == 0.5
    assert score.relevance == 0.5


async def test_score_turn_clamps_values_above_ten() -> None:
    provider = _fake_provider('{"coherence": 999, "relevance": 999}')
    evaluator = MultiTurnEvaluator(provider)
    turn = Turn(role="assistant", content="reply")

    score = await evaluator._score_turn(history=[], turn=turn, turn_index=0)

    assert score.coherence == 1.0
    assert score.relevance == 1.0


async def test_score_turn_handles_provider_exception() -> None:
    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=RuntimeError("network error"))
    evaluator = MultiTurnEvaluator(provider)
    turn = Turn(role="assistant", content="reply")

    score = await evaluator._score_turn(history=[], turn=turn, turn_index=1)

    assert score.coherence == 0.5
    assert score.relevance == 0.5


# ---------------------------------------------------------------------------
# _judge_goal
# ---------------------------------------------------------------------------


async def test_judge_goal_all_criteria_met() -> None:
    provider = _fake_provider()
    evaluator = MultiTurnEvaluator(provider)

    achieved, met, failed = await evaluator._judge_goal(
        final_reply="Python is great and I asked a clarifying question",
        expected="python clarifying",
        criteria=["python", "clarifying question"],
    )

    assert achieved is True
    assert met == ["python", "clarifying question"]
    assert failed == []


async def test_judge_goal_empty_expected_not_achieved() -> None:
    provider = _fake_provider()
    evaluator = MultiTurnEvaluator(provider)

    achieved, met, failed = await evaluator._judge_goal(
        final_reply="anything", expected="", criteria=[]
    )

    assert achieved is False
    assert met == []
    assert failed == []


async def test_judge_goal_no_criteria_returns_empty_lists() -> None:
    provider = _fake_provider()
    evaluator = MultiTurnEvaluator(provider)

    achieved, met, failed = await evaluator._judge_goal(
        final_reply="the goal is achieved", expected="goal achieved", criteria=[]
    )

    assert achieved is True
    assert met == []
    assert failed == []


@pytest.mark.parametrize("expected", ["ALPHA BETA", "alpha beta"])
async def test_judge_goal_case_insensitive_match(expected: str) -> None:
    provider = _fake_provider()
    evaluator = MultiTurnEvaluator(provider)

    achieved, _, _ = await evaluator._judge_goal(
        final_reply="the answer involves Alpha somehow", expected=expected, criteria=[]
    )
    assert achieved is True
