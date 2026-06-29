"""Comprehensive tests for SelfOptimizerV2 — TenantOptimizationState, Bayesian A/B,
apply_suggestion, rollback, arm assignment, compute_delta, list_experiments.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.intelligence.self_optimizer_v2 import (
    DEFAULT_MIN_GOALS,
    DOMAIN_METRICS,
    SelfOptimizerV2,
    TenantOptimizationState,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_redis(data: dict | None = None) -> AsyncMock:
    store: dict = dict(data or {})
    redis = AsyncMock()

    async def _get(key):
        return store.get(key)

    async def _setex(key, ttl, val):
        store[key] = val

    redis.get = AsyncMock(side_effect=_get)
    redis.setex = AsyncMock(side_effect=_setex)
    return redis


def _make_db_session(fetchone_return=None, fetchall_return=None):
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone = MagicMock(return_value=fetchone_return)
    mock_result.fetchall = MagicMock(return_value=fetchall_return or [])
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


def _make_optimizer(redis=None, db_session=None, llm_factory=None):
    if redis is None:
        redis = _make_redis()

    def db_factory():
        return db_session or _make_db_session()

    if llm_factory is None:
        llm_factory = AsyncMock(return_value=AsyncMock())

    return SelfOptimizerV2(redis=redis, db_factory=db_factory, llm_provider_factory=llm_factory)


# ── 1. TenantOptimizationState ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_state_get_default():
    redis = _make_redis()
    state = TenantOptimizationState(redis)
    result = await state.get("t1", "a1")
    assert result["goals_completed"] == 0
    assert result["current_experiment_id"] is None
    assert result["last_optimized_at"] is None


@pytest.mark.asyncio
async def test_state_get_existing():
    data = json.dumps({"goals_completed": 5, "current_experiment_id": "exp1", "last_optimized_at": None})
    redis = _make_redis({"optstate:t1:a1": data})
    state = TenantOptimizationState(redis)
    result = await state.get("t1", "a1")
    assert result["goals_completed"] == 5
    assert result["current_experiment_id"] == "exp1"


@pytest.mark.asyncio
async def test_state_update():
    redis = _make_redis()
    state = TenantOptimizationState(redis)
    await state.update("t1", "a1", {"goals_completed": 10})
    result = await state.get("t1", "a1")
    assert result["goals_completed"] == 10


@pytest.mark.asyncio
async def test_state_increment_goals():
    redis = _make_redis()
    state = TenantOptimizationState(redis)
    count1 = await state.increment_goals("t1", "a1")
    count2 = await state.increment_goals("t1", "a1")
    assert count1 == 1
    assert count2 == 2


@pytest.mark.asyncio
async def test_state_key_format():
    redis = _make_redis()
    state = TenantOptimizationState(redis)
    key = state._key("t1", "a1")
    assert key == "optstate:t1:a1"


# ── 2. DEFAULT_MIN_GOALS ──────────────────────────────────────────────────────

def test_default_min_goals_is_five():
    assert DEFAULT_MIN_GOALS == 5


# ── 3. DOMAIN_METRICS ────────────────────────────────────────────────────────

def test_domain_metrics_coverage():
    assert "legal" in DOMAIN_METRICS
    assert "healthcare" in DOMAIN_METRICS
    assert "finance" in DOMAIN_METRICS
    assert "education" in DOMAIN_METRICS
    assert "ecommerce" in DOMAIN_METRICS


# ── 4. _compute_delta ────────────────────────────────────────────────────────

def test_compute_delta_changed_field():
    before = {"prompt": "old", "max_iter": 5}
    after = {"prompt": "new", "max_iter": 5}
    delta = SelfOptimizerV2._compute_delta(before, after)
    assert "prompt" in delta
    assert delta["prompt"]["before"] == "old"
    assert delta["prompt"]["after"] == "new"
    assert "max_iter" not in delta


def test_compute_delta_added_field():
    before = {}
    after = {"new_field": "val"}
    delta = SelfOptimizerV2._compute_delta(before, after)
    assert "new_field" in delta
    assert delta["new_field"]["before"] is None
    assert delta["new_field"]["after"] == "val"


def test_compute_delta_removed_field():
    before = {"old_field": "val"}
    after = {}
    delta = SelfOptimizerV2._compute_delta(before, after)
    assert "old_field" in delta
    assert delta["old_field"]["before"] == "val"
    assert delta["old_field"]["after"] is None


def test_compute_delta_no_changes():
    before = {"a": 1}
    after = {"a": 1}
    delta = SelfOptimizerV2._compute_delta(before, after)
    assert delta == {}


# ── 5. _apply_suggestion_to_config ────────────────────────────────────────────

def test_apply_suggestion_changes_field():
    config = {"system_prompt": "old prompt", "max_iter": 5}
    suggestion = {
        "suggested_change": {
            "field": "system_prompt",
            "new_value": "improved prompt",
        },
        "rationale": "better",
    }
    result = SelfOptimizerV2._apply_suggestion_to_config(config, suggestion)
    assert result["system_prompt"] == "improved prompt"
    assert result["max_iter"] == 5


def test_apply_suggestion_path_not_found_returns_unchanged():
    config = {"a": 1}
    suggestion = {
        "suggested_change": {
            "field": "b.c",  # nested path doesn't exist
            "new_value": "val",
        },
    }
    result = SelfOptimizerV2._apply_suggestion_to_config(config, suggestion)
    assert result == config


def test_apply_suggestion_no_field_returns_copy():
    config = {"a": 1}
    suggestion = {"suggested_change": {}}
    result = SelfOptimizerV2._apply_suggestion_to_config(config, suggestion)
    assert result == config


def test_apply_suggestion_no_change_key_returns_copy():
    config = {"a": 1}
    result = SelfOptimizerV2._apply_suggestion_to_config(config, {})
    assert result == config


# ── 6. _bayesian_prob_better ─────────────────────────────────────────────────

def test_bayesian_candidate_better():
    prob = SelfOptimizerV2._bayesian_prob_better(
        ctrl_n=100, ctrl_mean=0.5,
        cand_n=100, cand_mean=0.8,
    )
    assert prob > 0.8  # candidate should clearly win


def test_bayesian_control_better():
    prob = SelfOptimizerV2._bayesian_prob_better(
        ctrl_n=100, ctrl_mean=0.9,
        cand_n=100, cand_mean=0.3,
    )
    assert prob < 0.2  # candidate should clearly lose


def test_bayesian_equal_means_near_half():
    prob = SelfOptimizerV2._bayesian_prob_better(
        ctrl_n=100, ctrl_mean=0.6,
        cand_n=100, cand_mean=0.6,
    )
    assert 0.3 < prob < 0.7  # near 50%


def test_bayesian_zero_means():
    prob = SelfOptimizerV2._bayesian_prob_better(
        ctrl_n=1, ctrl_mean=0.0,
        cand_n=1, cand_mean=0.0,
    )
    assert 0.0 <= prob <= 1.0


# ── 7. apply_suggestion DB path ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_suggestion_success():
    redis = _make_redis()
    mock_session = _make_db_session(fetchone_return=(json.dumps({"system_prompt": "old"}),))
    opt = _make_optimizer(redis=redis, db_session=mock_session)

    result = await opt.apply_suggestion("t1", "a1", "exp1", {"system_prompt": "new"})
    assert result is True


@pytest.mark.asyncio
async def test_apply_suggestion_db_error_returns_false():
    redis = _make_redis()
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("DB error"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    def db_factory():
        return mock_session

    opt = SelfOptimizerV2(redis=redis, db_factory=db_factory, llm_provider_factory=AsyncMock())
    result = await opt.apply_suggestion("t1", "a1", "exp1", {"system_prompt": "new"})
    assert result is False


# ── 8. rollback ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rollback_success():
    redis = _make_redis()
    mock_session = _make_db_session(fetchone_return=(json.dumps({"system_prompt": "original"}),))
    opt = _make_optimizer(redis=redis, db_session=mock_session)

    result = await opt.rollback("t1", "a1", "exp1", "performance_degradation")
    assert result is True


@pytest.mark.asyncio
async def test_rollback_no_experiment_returns_false():
    redis = _make_redis()
    mock_session = _make_db_session(fetchone_return=None)
    opt = _make_optimizer(redis=redis, db_session=mock_session)

    result = await opt.rollback("t1", "a1", "exp-notfound", "reason")
    assert result is False


@pytest.mark.asyncio
async def test_rollback_db_error_returns_false():
    redis = _make_redis()
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("DB error"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    def db_factory():
        return mock_session

    opt = SelfOptimizerV2(redis=redis, db_factory=db_factory, llm_provider_factory=AsyncMock())
    result = await opt.rollback("t1", "a1", "exp1", "reason")
    assert result is False


# ── 9. _get_arm_for_goal ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_arm_no_experiment_returns_control():
    redis = _make_redis()
    opt = _make_optimizer(redis=redis)

    arm = await opt._get_arm_for_goal("t1", "a1", "goal-abc")
    assert arm == "control"


@pytest.mark.asyncio
async def test_get_arm_deterministic():
    redis = _make_redis()
    state_data = json.dumps({
        "goals_completed": 10,
        "current_experiment_id": "exp1",
        "last_optimized_at": None,
    })
    redis_with_state = _make_redis({"optstate:t1:a1": state_data})

    mock_session = _make_db_session(fetchone_return=(50,))  # 50% split
    opt = _make_optimizer(redis=redis_with_state, db_session=mock_session)

    arm1 = await opt._get_arm_for_goal("t1", "a1", "goal-same")
    arm2 = await opt._get_arm_for_goal("t1", "a1", "goal-same")
    assert arm1 == arm2


# ── 10. list_experiments ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_experiments_no_db_returns_empty():
    redis = _make_redis()
    opt = SelfOptimizerV2(redis=redis, db_factory=None, llm_provider_factory=AsyncMock())  # type: ignore[arg-type]
    result = await opt.list_experiments("t1")
    assert result == []


@pytest.mark.asyncio
async def test_list_experiments_with_db():
    redis = _make_redis()
    mock_session = _make_db_session(fetchall_return=[
        ("exp1", "a1", "running", "{}", "{}", 5, 3, "2026-01-01", None, None)
    ])
    opt = _make_optimizer(redis=redis, db_session=mock_session)
    result = await opt.list_experiments("t1")
    assert len(result) == 1
    assert result[0]["id"] == "exp1"


# ── 11. on_goal_completed ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_on_goal_completed_increments_count():
    redis = _make_redis()
    mock_session = _make_db_session()
    opt = _make_optimizer(redis=redis, db_session=mock_session)

    await opt.on_goal_completed(
        tenant_id="t1",
        agent_id="a1",
        goal_id="g1",
        eval_score=0.8,
        cost_usd=0.05,
        latency_ms=1000,
    )
    state = await opt._state.get("t1", "a1")
    assert state["goals_completed"] == 1


# ── 12. _get_min_goals ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_min_goals_default():
    redis = _make_redis()
    mock_session = _make_db_session(fetchone_return=(None,))
    opt = _make_optimizer(redis=redis, db_session=mock_session)

    result = await opt._get_min_goals("t1")
    assert result == DEFAULT_MIN_GOALS


@pytest.mark.asyncio
async def test_get_min_goals_from_db():
    redis = _make_redis()
    mock_session = _make_db_session(fetchone_return=("20",))
    opt = _make_optimizer(redis=redis, db_session=mock_session)

    result = await opt._get_min_goals("t1")
    assert result == 20
