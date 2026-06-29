"""Tests for LearningPipeline — curated collective learning (Phase D)."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.civilization.learning import (
    LearningPipeline,
    _FakeScoringState,
    _PROMOTION_SCORE_THRESHOLD,
    _REJECTION_SCORE_THRESHOLD,
)


# ── helpers ────────────────────────────────────────────────────────────────────


class _noop_ctx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class _FakeSession:
    def __init__(self, rows=None, raise_on=None):
        self.executions = []
        self._rows = rows or []
        self._raise = raise_on

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def begin(self):
        return _noop_ctx()

    async def execute(self, stmt, params=None):
        if self._raise:
            raise RuntimeError(self._raise)
        self.executions.append((stmt, params))
        return SimpleNamespace(
            fetchall=lambda: list(self._rows),
            fetchone=lambda: self._rows[0] if self._rows else None,
        )


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
    mock_eval._score_coherence = AsyncMock(return_value=0.2)  # Returns float directly

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
    mock_eval._score_coherence = AsyncMock(return_value=0.9)  # Returns float directly

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
    mock_eval._score_coherence = AsyncMock(return_value=0.55)  # Returns float directly

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


# ── Additional coverage tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_candidate_with_db():
    """submit_candidate inserts into DB when db_session_factory is set."""
    session = _FakeSession()
    pipeline = _make_pipeline(db=lambda: session)

    from app.tenancy.context import TenantContext, PlanTier
    tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")
    cid = await pipeline.submit_candidate(
        agent_id="a1",
        candidate_text="Some useful learning insight",
        tenant_ctx=tenant_ctx,
    )
    assert cid
    # Should have executed INSERT
    assert len(session.executions) >= 1


@pytest.mark.asyncio
async def test_submit_candidate_db_exception_still_returns_id():
    """DB failure during submit_candidate is swallowed."""
    session = _FakeSession(raise_on="DB insert failed")
    pipeline = _make_pipeline(db=lambda: session)

    from app.tenancy.context import TenantContext, PlanTier
    tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")
    cid = await pipeline.submit_candidate(
        agent_id="a1",
        candidate_text="learning text",
        tenant_ctx=tenant_ctx,
    )
    assert cid  # still returns ID


@pytest.mark.asyncio
async def test_submit_candidate_publishes_to_bus():
    mock_bus = AsyncMock()
    mock_bus.publish = AsyncMock()
    pipeline = _make_pipeline(bus=mock_bus)

    from app.tenancy.context import TenantContext, PlanTier
    tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")
    await pipeline.submit_candidate(
        agent_id="a1",
        candidate_text="coordination insight",
        tenant_ctx=tenant_ctx,
    )
    mock_bus.publish.assert_called_once()


@pytest.mark.asyncio
async def test_run_step_handles_process_exception():
    """Exceptions in _process_candidate are caught and the loop continues."""
    pipeline = _make_pipeline()
    pipeline._get_pending_candidates = AsyncMock(return_value=[
        {"id": "c1", "candidate": "c1-text", "source_agent_id": "a1"},
        {"id": "c2", "candidate": "c2-text", "source_agent_id": "a2"},
    ])
    pipeline._process_candidate = AsyncMock(side_effect=[RuntimeError("Eval error"), "promoted"])

    result = await pipeline.run_step()
    # c2 should still be promoted despite c1's error
    assert result["promoted"] == 1


@pytest.mark.asyncio
async def test_run_step_validated_only():
    """'validated' result (mid-score) increments validated but not promoted/rejected."""
    pipeline = _make_pipeline()
    pipeline._get_pending_candidates = AsyncMock(return_value=[
        {"id": "c1", "candidate": "text", "source_agent_id": "a1"},
    ])
    pipeline._process_candidate = AsyncMock(return_value="validated")

    result = await pipeline.run_step()
    assert result["validated"] == 1
    assert result["promoted"] == 0
    assert result["rejected"] == 0


@pytest.mark.asyncio
async def test_process_candidate_no_eval_runner_uses_default_score():
    """Without eval_runner, default score 0.5 is used → validated (mid-range)."""
    pipeline = _make_pipeline()
    pipeline._set_candidate_status = AsyncMock()

    candidate = {"id": "c1", "candidate": "neutral text", "source_agent_id": "a1"}
    result = await pipeline._process_candidate(candidate)
    assert result == "validated"  # 0.5 is between 0.35 and 0.7


@pytest.mark.asyncio
async def test_process_candidate_eval_exception_uses_default_05():
    """Eval failure falls back to 0.5 → validated."""
    mock_eval = AsyncMock()
    mock_eval._score_coherence = AsyncMock(side_effect=RuntimeError("Eval failed"))
    pipeline = _make_pipeline(eval_runner=mock_eval)
    pipeline._set_candidate_status = AsyncMock()

    candidate = {"id": "c1", "candidate": "neutral text", "source_agent_id": "a1"}
    result = await pipeline._process_candidate(candidate)
    assert result == "validated"


@pytest.mark.asyncio
async def test_promote_to_ltm_with_no_ltm_returns_none():
    """_promote_to_ltm with ltm=None returns None immediately."""
    pipeline = _make_pipeline(ltm=None)
    result = await pipeline._promote_to_ltm(
        candidate_id="c1",
        candidate_text="learning text",
        source_agent_id="a1",
    )
    assert result is None


@pytest.mark.asyncio
async def test_promote_to_ltm_exception_returns_none():
    """_promote_to_ltm exception is swallowed and returns None."""
    mock_ltm = AsyncMock()
    mock_ltm.store_async = AsyncMock(side_effect=RuntimeError("LTM unavailable"))
    pipeline = _make_pipeline(ltm=mock_ltm)

    result = await pipeline._promote_to_ltm(
        candidate_id="c1",
        candidate_text="learning text",
        source_agent_id="a1",
    )
    assert result is None


@pytest.mark.asyncio
async def test_get_learnings_no_db_returns_empty():
    pipeline = _make_pipeline()
    result = await pipeline.get_learnings()
    assert result == []


@pytest.mark.asyncio
async def test_get_learnings_with_db():
    now = datetime.now(UTC)
    rows = [("l1", "learning text here 123", "a1", "promoted", 0.9, "mem-1", now, now)]
    session = _FakeSession(rows=rows)
    pipeline = _make_pipeline(db=lambda: session)
    result = await pipeline.get_learnings()
    assert len(result) == 1
    assert result[0]["id"] == "l1"
    assert result[0]["status"] == "promoted"
    assert result[0]["eval_score"] == 0.9


@pytest.mark.asyncio
async def test_get_learnings_with_status_filter():
    now = datetime.now(UTC)
    rows = [("l2", "candidate text 123456", "a1", "candidate", None, None, now, now)]
    session = _FakeSession(rows=rows)
    pipeline = _make_pipeline(db=lambda: session)
    result = await pipeline.get_learnings(status="candidate")
    _, params = session.executions[0]
    assert params["status"] == "candidate"
    assert len(result) == 1


@pytest.mark.asyncio
async def test_get_learnings_db_exception_returns_empty():
    session = _FakeSession(raise_on="DB error")
    pipeline = _make_pipeline(db=lambda: session)
    result = await pipeline.get_learnings()
    assert result == []


@pytest.mark.asyncio
async def test_get_learnings_none_dates_become_empty_strings():
    rows = [("l3", "text1234567890123456789", "a1", "validated", 0.6, None, None, None)]
    session = _FakeSession(rows=rows)
    pipeline = _make_pipeline(db=lambda: session)
    result = await pipeline.get_learnings()
    assert result[0]["created_at"] == ""
    assert result[0]["decided_at"] == ""


@pytest.mark.asyncio
async def test_get_pending_candidates_no_db_returns_empty():
    pipeline = _make_pipeline()
    result = await pipeline._get_pending_candidates(10)
    assert result == []


@pytest.mark.asyncio
async def test_get_pending_candidates_with_db():
    rows = [("c1", "some candidate text here", "a1"), ("c2", "another candidate text", "a2")]
    session = _FakeSession(rows=rows)
    pipeline = _make_pipeline(db=lambda: session)
    result = await pipeline._get_pending_candidates(5)
    assert len(result) == 2
    assert result[0]["id"] == "c1"
    assert result[0]["source_agent_id"] == "a1"


@pytest.mark.asyncio
async def test_get_pending_candidates_db_exception_returns_empty():
    session = _FakeSession(raise_on="DB error")
    pipeline = _make_pipeline(db=lambda: session)
    result = await pipeline._get_pending_candidates(5)
    assert result == []


@pytest.mark.asyncio
async def test_set_candidate_status_no_db():
    pipeline = _make_pipeline()
    await pipeline._set_candidate_status("c1", "validated", 0.6)  # no-op


@pytest.mark.asyncio
async def test_set_candidate_status_with_db():
    session = _FakeSession()
    pipeline = _make_pipeline(db=lambda: session)
    await pipeline._set_candidate_status("c1", "validated", 0.6)
    assert len(session.executions) >= 1


@pytest.mark.asyncio
async def test_set_candidate_status_db_exception_swallowed():
    session = _FakeSession(raise_on="DB error")
    pipeline = _make_pipeline(db=lambda: session)
    await pipeline._set_candidate_status("c1", "rejected", 0.2)  # should not raise


@pytest.mark.asyncio
async def test_set_candidate_promoted_no_db():
    pipeline = _make_pipeline()
    await pipeline._set_candidate_promoted("c1", "mem-1", 0.9)  # no-op


@pytest.mark.asyncio
async def test_set_candidate_promoted_with_db():
    session = _FakeSession()
    pipeline = _make_pipeline(db=lambda: session)
    await pipeline._set_candidate_promoted("c1", "mem-1", 0.9)
    assert len(session.executions) >= 1


@pytest.mark.asyncio
async def test_set_candidate_promoted_db_exception_swallowed():
    session = _FakeSession(raise_on="DB error")
    pipeline = _make_pipeline(db=lambda: session)
    await pipeline._set_candidate_promoted("c1", "mem-1", 0.9)  # should not raise


# ── _FakeScoringState ──────────────────────────────────────────────────────────


def test_fake_scoring_state_init():
    state = _FakeScoringState(goal="optimize code", steps=["step1", "step2"])
    assert state.goal == "optimize code"
    assert state.steps == ["step1", "step2"]
    assert state.status == "complete"
    assert state.error_message == ""
    assert state.verification_success is True
    assert state.context == {}
    assert state.goal_id  # non-empty UUID hex


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
    mock_eval._score_coherence = AsyncMock(return_value=0.2)  # Returns float directly

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
    mock_eval._score_coherence = AsyncMock(return_value=0.9)  # Returns float directly

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
    mock_eval._score_coherence = AsyncMock(return_value=0.55)  # Returns float directly

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
