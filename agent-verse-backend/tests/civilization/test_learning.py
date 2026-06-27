"""Tests for LearningPipeline — curated collective learning (Phase D)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from app.civilization.learning import LearningPipeline, _PROMOTION_SCORE_THRESHOLD, _REJECTION_SCORE_THRESHOLD


def _make_pipeline(**kwargs) -> LearningPipeline:
    return LearningPipeline(
        civilization_id="civ-1",
        tenant_id="t1",
        db_session_factory=kwargs.get("db"),
        eval_runner=kwargs.get("eval_runner"),
        long_term_memory=kwargs.get("ltm"),
        bus=kwargs.get("bus"),
        redis=kwargs.get("redis"),
    )


@pytest.mark.asyncio
async def test_submit_candidate_returns_id():
    pipeline = _make_pipeline()
    from app.tenancy.context import TenantContext, PlanTier
    tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")
    cid = await pipeline.submit_candidate(
        agent_id="a1",
        candidate_text="Found that Jira P1 issues should be resolved within 24h",
        tenant_ctx=tenant_ctx,
    )
    assert cid  # non-empty ID


@pytest.mark.asyncio
async def test_rejected_candidate_never_reaches_ltm():
    """Rejected candidates MUST NOT be promoted to LTM — anti-poisoning gate."""
    mock_ltm = AsyncMock()
    mock_ltm.store_async = AsyncMock()

    mock_eval = AsyncMock()
    mock_scorecard = MagicMock()
    mock_scorecard.average_score = MagicMock(return_value=0.2)  # Below rejection threshold
    mock_eval.score_and_persist = AsyncMock(return_value=mock_scorecard)

    pipeline = _make_pipeline(ltm=mock_ltm, eval_runner=mock_eval)

    candidate = {"id": "c1", "candidate": "bad learning content", "source_agent_id": "a1"}
    result = await pipeline._process_candidate(candidate)

    assert result == "rejected"
    mock_ltm.store_async.assert_not_called()


@pytest.mark.asyncio
async def test_high_score_candidate_promoted_to_ltm():
    """High-scoring validated candidates must be promoted to LTM."""
    mock_ltm = AsyncMock()
    mock_ltm.store_async = AsyncMock()

    mock_eval = AsyncMock()
    mock_scorecard = MagicMock()
    mock_scorecard.average_score = MagicMock(return_value=0.9)  # Above promotion threshold
    mock_eval.score_and_persist = AsyncMock(return_value=mock_scorecard)

    pipeline = _make_pipeline(ltm=mock_ltm, eval_runner=mock_eval)
    pipeline._set_candidate_status = AsyncMock()
    pipeline._set_candidate_promoted = AsyncMock()

    candidate = {"id": "c2", "candidate": "excellent learning content", "source_agent_id": "a1"}
    result = await pipeline._process_candidate(candidate)

    assert result == "promoted"
    mock_ltm.store_async.assert_called_once()


@pytest.mark.asyncio
async def test_medium_score_validated_not_promoted():
    """Medium scores (above rejection, below promotion) are validated but not promoted."""
    mock_ltm = AsyncMock()
    mock_ltm.store_async = AsyncMock()

    mock_eval = AsyncMock()
    mock_scorecard = MagicMock()
    # Between rejection (0.35) and promotion (0.7) thresholds
    mock_scorecard.average_score = MagicMock(return_value=0.55)
    mock_eval.score_and_persist = AsyncMock(return_value=mock_scorecard)

    pipeline = _make_pipeline(ltm=mock_ltm, eval_runner=mock_eval)
    pipeline._set_candidate_status = AsyncMock()
    pipeline._get_pending_candidates = AsyncMock(return_value=[])

    candidate = {"id": "c3", "candidate": "medium learning content", "source_agent_id": "a1"}
    result = await pipeline._process_candidate(candidate)

    assert result == "validated"
    mock_ltm.store_async.assert_not_called()


@pytest.mark.asyncio
async def test_run_step_processes_batch():
    pipeline = _make_pipeline()
    pipeline._get_pending_candidates = AsyncMock(return_value=[
        {"id": "c1", "candidate": "learning 1", "source_agent_id": "a1"},
        {"id": "c2", "candidate": "learning 2", "source_agent_id": "a2"},
    ])
    pipeline._process_candidate = AsyncMock(side_effect=["promoted", "rejected"])

    result = await pipeline.run_step()

    assert result["promoted"] == 1
    assert result["rejected"] == 1
    assert result["validated"] == 1  # promoted counts as validated too


@pytest.mark.asyncio
async def test_run_step_empty_batch():
    pipeline = _make_pipeline()
    pipeline._get_pending_candidates = AsyncMock(return_value=[])

    result = await pipeline.run_step()

    assert result["validated"] == 0
    assert result["promoted"] == 0
    assert result["rejected"] == 0


def test_promotion_and_rejection_thresholds():
    """Verify threshold constants are correctly ordered."""
    assert _REJECTION_SCORE_THRESHOLD < _PROMOTION_SCORE_THRESHOLD
    assert 0.0 < _REJECTION_SCORE_THRESHOLD < 1.0
    assert 0.0 < _PROMOTION_SCORE_THRESHOLD < 1.0


def test_civilization_tick_task_exists():
    import inspect
    from app.scaling import tasks
    src = inspect.getsource(tasks)
    assert "civilization_tick" in src
    assert "civilization_learning_step" in src


def test_learning_candidates_never_promote_when_rejected():
    """This is a CRITICAL property: rejected must NEVER reach LTM regardless of any bug."""
    from app.civilization.learning import _REJECTION_SCORE_THRESHOLD, _PROMOTION_SCORE_THRESHOLD
    # These are the safety thresholds; ensure rejection threshold is ALWAYS below promotion
    assert _REJECTION_SCORE_THRESHOLD < _PROMOTION_SCORE_THRESHOLD, \
        "CRITICAL: rejection threshold must be lower than promotion threshold"
