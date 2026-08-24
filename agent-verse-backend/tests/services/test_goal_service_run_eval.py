"""Tests for GoalService.run_eval (line 2782 of app/services/goal_service.py)
and related goal-service methods flagged as untested.

run_eval is a public method that runs EvalRunner.score_async and caches the
scorecard. The existing 13 goal_service test files don't exercise this path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.state import GoalStatus
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(
    tenant_id="tid-run-eval", plan=PlanTier.ENTERPRISE, api_key_id="kid-run-eval"
)


def _scorecard_mock(goal_id: str = "goal-x") -> MagicMock:
    """Build a mock scorecard with the attributes run_eval returns."""
    sc = MagicMock(name="scorecard")
    sc.goal_id = goal_id
    sc.scores = {"correctness": 0.9, "efficiency": 0.85}
    sc.average_score = MagicMock(return_value=0.875)
    sc.passed = MagicMock(return_value=True)
    sc.iterations = 3
    return sc


def _record(
    *,
    goal_id: str = "goal-x",
    goal_text: str = "Deploy to prod",
    status: GoalStatus = GoalStatus.COMPLETE,
    execution_context: dict[str, Any] | None = None,
) -> GoalRecord:
    """Build a real GoalRecord (no agent_state field — run_eval reconstructs state)."""
    return GoalRecord(
        goal_id=goal_id,
        goal_text=goal_text,
        status=status,
        tenant_id=_CTX.tenant_id,
        priority="high",
        dry_run=False,
        created_at=datetime.now(timezone.utc).isoformat(),
        execution_context=execution_context
        if execution_context is not None
        else {"verification_feedback": "ok", "iterations": 2},
    )


# ── run_eval success paths ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_eval_success_reconstructs_state() -> None:
    """run_eval reconstructs AgentState from the record (since GoalRecord has
    no agent_state field) and caches the scorecard."""
    svc = GoalService()
    record = _record(goal_id="goal-x")
    svc._goals["goal-x"] = record

    scorecard = _scorecard_mock(goal_id="goal-x")
    mock_runner_instance = MagicMock()
    mock_runner_instance.score_async = AsyncMock(return_value=scorecard)
    svc._app_provider = MagicMock(name="app_provider")

    with patch("app.intelligence.eval_runner.EvalRunner", return_value=mock_runner_instance):
        result = await svc.run_eval("goal-x", _CTX)

    assert result["status"] == "evaluated"
    assert result["goal_id"] == "goal-x"
    assert result["scores"] == {"correctness": 0.9, "efficiency": 0.85}
    assert result["average_score"] == 0.875
    assert result["passed"] is True
    assert result["iterations"] == 3
    # Scorecard should be cached for future GET /eval/{goal_id}
    assert svc._eval_scores["goal-x"] is scorecard
    # State was reconstructed and passed to score_async
    call_kwargs = mock_runner_instance.score_async.call_args.kwargs
    assert "state" in call_kwargs
    assert call_kwargs["state"].goal_id == "goal-x"
    assert call_kwargs["state"].goal == "Deploy to prod"
    assert call_kwargs["tenant_ctx"] is _CTX


@pytest.mark.asyncio
async def test_run_eval_invalid_status_falls_back_to_complete() -> None:
    """run_eval handles invalid status values by defaulting to GoalStatus.COMPLETE.
    The record stores a valid GoalStatus enum, so we construct with a non-mapped
    status value to exercise the ValueError fallback in the try/except block."""
    svc = GoalService()
    # Build a record with a status that won't be in the GoalStatus enum mapping
    record = _record(goal_id="goal-z", status=GoalStatus.PLANNING)
    # Override status.value to a string that doesn't map to any GoalStatus
    record.status = MagicMock()
    record.status.value = "weird-status"  # not a valid GoalStatus
    svc._goals["goal-z"] = record

    scorecard = _scorecard_mock(goal_id="goal-z")
    mock_runner_instance = MagicMock()
    mock_runner_instance.score_async = AsyncMock(return_value=scorecard)

    with patch("app.intelligence.eval_runner.EvalRunner", return_value=mock_runner_instance):
        result = await svc.run_eval("goal-z", _CTX)

    assert result["status"] == "evaluated"
    call_kwargs = mock_runner_instance.score_async.call_args.kwargs
    # State should have been reconstructed with COMPLETE status fallback
    assert call_kwargs["state"].status == GoalStatus.COMPLETE


@pytest.mark.asyncio
async def test_run_eval_non_dict_execution_context_uses_defaults() -> None:
    """run_eval handles execution_context that is not a dict by using default values."""
    svc = GoalService()
    # Build a record normally, then override execution_context to a non-dict
    record = _record(goal_id="goal-w", execution_context={"iterations": 1})
    record.execution_context = None  # set to non-dict
    svc._goals["goal-w"] = record

    scorecard = _scorecard_mock(goal_id="goal-w")
    mock_runner_instance = MagicMock()
    mock_runner_instance.score_async = AsyncMock(return_value=scorecard)

    with patch("app.intelligence.eval_runner.EvalRunner", return_value=mock_runner_instance):
        result = await svc.run_eval("goal-w", _CTX)

    assert result["status"] == "evaluated"
    call_kwargs = mock_runner_instance.score_async.call_args.kwargs
    # Reconstructed state should have empty verification_feedback and 1 iteration
    assert call_kwargs["state"].verification_feedback == ""
    assert call_kwargs["state"].iterations == 1
    # Context should fall back to empty dict
    assert call_kwargs["state"].context == {}


@pytest.mark.asyncio
async def test_run_eval_goal_not_found_raises() -> None:
    """run_eval raises NotFoundError when the goal record is not found."""
    from app.core.errors import NotFoundError

    svc = GoalService()
    # Don't insert any record → _get_record should raise NotFoundError
    with pytest.raises(NotFoundError):
        await svc.run_eval("nonexistent-goal", _CTX)


@pytest.mark.asyncio
async def test_run_eval_passes_app_provider_to_score_async() -> None:
    """run_eval forwards self._app_provider to EvalRunner.score_async."""
    svc = GoalService()
    record = _record(goal_id="goal-prov")
    svc._goals["goal-prov"] = record

    mock_provider = MagicMock(name="app_provider")
    svc._app_provider = mock_provider

    scorecard = _scorecard_mock(goal_id="goal-prov")
    mock_runner_instance = MagicMock()
    mock_runner_instance.score_async = AsyncMock(return_value=scorecard)

    with patch("app.intelligence.eval_runner.EvalRunner", return_value=mock_runner_instance):
        await svc.run_eval("goal-prov", _CTX)

    call_kwargs = mock_runner_instance.score_async.call_args.kwargs
    assert call_kwargs["provider"] is mock_provider


@pytest.mark.asyncio
async def test_run_eval_no_app_provider_passes_none() -> None:
    """When _app_provider is unset, run_eval passes provider=None to score_async."""
    svc = GoalService()
    record = _record(goal_id="goal-no-prov")
    svc._goals["goal-no-prov"] = record

    # Ensure _app_provider is not set
    if hasattr(svc, "_app_provider"):
        del svc._app_provider

    scorecard = _scorecard_mock(goal_id="goal-no-prov")
    mock_runner_instance = MagicMock()
    mock_runner_instance.score_async = AsyncMock(return_value=scorecard)

    with patch("app.intelligence.eval_runner.EvalRunner", return_value=mock_runner_instance):
        await svc.run_eval("goal-no-prov", _CTX)

    call_kwargs = mock_runner_instance.score_async.call_args.kwargs
    # provider kwarg should be None (default when _app_provider not set)
    assert call_kwargs["provider"] is None


@pytest.mark.asyncio
async def test_run_eval_with_steps_and_events_in_record() -> None:
    """run_eval reconstructs state with steps and events when present."""
    svc = GoalService()
    record = _record(goal_id="goal-steps")
    record.steps = [
        MagicMock(status="completed", name="step1"),
        MagicMock(status="completed", name="step2"),
    ]
    record.events = [{"type": "step_completed"}]
    svc._goals["goal-steps"] = record

    scorecard = _scorecard_mock(goal_id="goal-steps")
    mock_runner_instance = MagicMock()
    mock_runner_instance.score_async = AsyncMock(return_value=scorecard)

    with patch("app.intelligence.eval_runner.EvalRunner", return_value=mock_runner_instance):
        result = await svc.run_eval("goal-steps", _CTX)

    assert result["status"] == "evaluated"
    call_kwargs = mock_runner_instance.score_async.call_args.kwargs
    state = call_kwargs["state"]
    assert len(state.steps) == 2
    assert len(state.events) == 1


@pytest.mark.asyncio
async def test_run_eval_caches_scorecard_for_subsequent_get_eval() -> None:
    """After run_eval completes, the scorecard is cached so GET /eval/{goal_id}
    can return it without recomputing."""
    svc = GoalService()
    record = _record(goal_id="goal-cache")
    svc._goals["goal-cache"] = record

    scorecard = _scorecard_mock(goal_id="goal-cache")
    mock_runner_instance = MagicMock()
    mock_runner_instance.score_async = AsyncMock(return_value=scorecard)

    with patch("app.intelligence.eval_runner.EvalRunner", return_value=mock_runner_instance):
        await svc.run_eval("goal-cache", _CTX)

    # Scorecard should be in _eval_scores cache
    assert "goal-cache" in svc._eval_scores
    assert svc._eval_scores["goal-cache"] is scorecard


@pytest.mark.asyncio
async def test_run_eval_evalrunner_score_async_exception_propagates() -> None:
    """If EvalRunner.score_async raises, run_eval propagates the exception
    (no swallowing) — caller is responsible for handling."""
    svc = GoalService()
    record = _record(goal_id="goal-err")
    svc._goals["goal-err"] = record

    mock_runner_instance = MagicMock()
    mock_runner_instance.score_async = AsyncMock(
        side_effect=RuntimeError("eval runner crashed")
    )

    with patch("app.intelligence.eval_runner.EvalRunner", return_value=mock_runner_instance):
        with pytest.raises(RuntimeError, match="eval runner crashed"):
            await svc.run_eval("goal-err", _CTX)


@pytest.mark.asyncio
async def test_run_eval_returns_correct_iteration_count_from_scorecard() -> None:
    """run_eval returns scorecard.iterations, not the record's iteration count."""
    svc = GoalService()
    record = _record(
        goal_id="goal-iter",
        execution_context={"iterations": 99},  # record has 99
    )
    svc._goals["goal-iter"] = record

    # Scorecard reports 5 iterations
    scorecard = _scorecard_mock(goal_id="goal-iter")
    scorecard.iterations = 5
    mock_runner_instance = MagicMock()
    mock_runner_instance.score_async = AsyncMock(return_value=scorecard)

    with patch("app.intelligence.eval_runner.EvalRunner", return_value=mock_runner_instance):
        result = await svc.run_eval("goal-iter", _CTX)

    # Should return scorecard.iterations (5), not record's (99)
    assert result["iterations"] == 5
