"""Tests for EvalDatasetBuilder (app/evals/dataset_builder.py).

Prior coverage exercised this only incidentally through the promotion-gate
integration test (tests/evals/test_eval_gates_promotion.py), which checks a
single below-threshold and a single above-threshold call. This file is the
dedicated unit-test surface for the dataset-construction logic itself: the
exact `_CANDIDATE_THRESHOLD` boundary, the `_infer_expected` status branching,
goal-text truncation, the returned dict's shape, and field propagation.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.state import GoalStatus
from app.evals.dataset_builder import _CANDIDATE_THRESHOLD, EvalDatasetBuilder


def _state(
    *,
    goal_id: str = "g-1",
    goal: str = "Deploy the service",
    tenant_id: str = "tenant-1",
    status: GoalStatus = GoalStatus.FAILED,
) -> SimpleNamespace:
    return SimpleNamespace(
        goal_id=goal_id,
        goal=goal,
        tenant_ctx=SimpleNamespace(tenant_id=tenant_id),
        status=status,
    )


# ── threshold boundary ──────────────────────────────────────────────────────


def test_candidate_threshold_constant_is_point_six():
    """Locks in the documented threshold value so a silent change to it is
    caught by a failing test rather than only by production behavior drift."""
    assert _CANDIDATE_THRESHOLD == 0.6


def test_score_at_threshold_is_not_a_candidate():
    """`score >= _CANDIDATE_THRESHOLD` -> None: the boundary value itself
    (0.6) counts as passing, not failing."""
    builder = EvalDatasetBuilder()
    assert builder.maybe_create(state=_state(), score=0.6) is None


def test_score_just_below_threshold_is_a_candidate():
    builder = EvalDatasetBuilder()
    candidate = builder.maybe_create(state=_state(), score=0.599)
    assert candidate is not None
    assert candidate["score"] == 0.599


def test_score_just_above_threshold_is_not_a_candidate():
    builder = EvalDatasetBuilder()
    assert builder.maybe_create(state=_state(), score=0.601) is None


def test_perfect_score_is_not_a_candidate():
    builder = EvalDatasetBuilder()
    assert builder.maybe_create(state=_state(), score=1.0) is None


def test_zero_score_is_a_candidate():
    builder = EvalDatasetBuilder()
    candidate = builder.maybe_create(state=_state(), score=0.0)
    assert candidate is not None
    assert candidate["score"] == 0.0


def test_negative_score_is_a_candidate():
    """No lower bound enforced on score — still flags as a candidate rather
    than crashing on an out-of-range value."""
    builder = EvalDatasetBuilder()
    candidate = builder.maybe_create(state=_state(), score=-0.5)
    assert candidate is not None


# ── _infer_expected status branching ────────────────────────────────────────


def test_failed_status_infers_correctness_expectation():
    builder = EvalDatasetBuilder()
    candidate = builder.maybe_create(state=_state(status=GoalStatus.FAILED), score=0.2)
    assert candidate is not None
    assert candidate["expected_behavior"] == "Goal should complete successfully with correct output"


def test_non_failed_status_infers_quality_expectation():
    """A run that technically COMPLETEd but scored low (e.g. weak output
    quality, not an outright failure) gets the *other* expected-behavior
    message — this branch had zero coverage before."""
    builder = EvalDatasetBuilder()
    candidate = builder.maybe_create(state=_state(status=GoalStatus.COMPLETE), score=0.3)
    assert candidate is not None
    assert candidate["expected_behavior"] == "Goal should complete with higher quality score"


def test_cancelled_status_also_infers_quality_expectation():
    """Any non-FAILED status (not just COMPLETE) falls through to the generic
    quality-score message — CANCELLED is neither FAILED nor a "success"."""
    builder = EvalDatasetBuilder()
    candidate = builder.maybe_create(state=_state(status=GoalStatus.CANCELLED), score=0.1)
    assert candidate is not None
    assert candidate["expected_behavior"] == "Goal should complete with higher quality score"


# ── goal_text truncation ────────────────────────────────────────────────────


def test_goal_text_is_truncated_to_500_characters():
    long_goal = "x" * 600
    builder = EvalDatasetBuilder()
    candidate = builder.maybe_create(state=_state(goal=long_goal), score=0.1)
    assert candidate is not None
    assert len(candidate["goal_text"]) == 500
    assert candidate["goal_text"] == long_goal[:500]


def test_goal_text_under_limit_is_not_padded_or_altered():
    short_goal = "Find the open issues"
    builder = EvalDatasetBuilder()
    candidate = builder.maybe_create(state=_state(goal=short_goal), score=0.1)
    assert candidate is not None
    assert candidate["goal_text"] == short_goal


def test_goal_text_exactly_500_characters_is_unchanged():
    exact_goal = "y" * 500
    builder = EvalDatasetBuilder()
    candidate = builder.maybe_create(state=_state(goal=exact_goal), score=0.1)
    assert candidate is not None
    assert candidate["goal_text"] == exact_goal
    assert len(candidate["goal_text"]) == 500


# ── returned dict shape and field propagation ───────────────────────────────


def test_candidate_dict_has_exactly_the_expected_keys():
    builder = EvalDatasetBuilder()
    candidate = builder.maybe_create(state=_state(), score=0.2)
    assert candidate is not None
    assert set(candidate.keys()) == {
        "goal_id",
        "goal_text",
        "tenant_id",
        "final_status",
        "score",
        "expected_behavior",
        "regression_candidate",
    }


def test_regression_candidate_flag_is_always_true_when_returned():
    builder = EvalDatasetBuilder()
    candidate = builder.maybe_create(state=_state(), score=0.05)
    assert candidate is not None
    assert candidate["regression_candidate"] is True


def test_tenant_id_is_propagated_from_state_tenant_ctx():
    builder = EvalDatasetBuilder()
    candidate = builder.maybe_create(state=_state(tenant_id="acme-corp"), score=0.2)
    assert candidate is not None
    assert candidate["tenant_id"] == "acme-corp"


def test_goal_id_is_propagated_from_state():
    builder = EvalDatasetBuilder()
    candidate = builder.maybe_create(state=_state(goal_id="goal-xyz"), score=0.2)
    assert candidate is not None
    assert candidate["goal_id"] == "goal-xyz"


def test_final_status_is_the_status_enum_value_not_the_enum_member():
    builder = EvalDatasetBuilder()
    candidate = builder.maybe_create(state=_state(status=GoalStatus.FAILED), score=0.2)
    assert candidate is not None
    assert candidate["final_status"] == "failed"
    assert isinstance(candidate["final_status"], str)


def test_multiple_calls_produce_independent_candidates():
    """No shared mutable state leaks between calls on the same builder
    instance — each maybe_create() call is a pure function of its inputs."""
    builder = EvalDatasetBuilder()
    first = builder.maybe_create(state=_state(goal_id="g-a", goal="goal A"), score=0.1)
    second = builder.maybe_create(state=_state(goal_id="g-b", goal="goal B"), score=0.2)
    assert first is not None
    assert second is not None
    assert first["goal_id"] == "g-a"
    assert second["goal_id"] == "g-b"
    assert first["goal_text"] == "goal A"
    assert second["goal_text"] == "goal B"
