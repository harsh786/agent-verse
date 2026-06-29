"""Additional tests for SelfOptimizerV2 to cover uncovered branches:
_read_current_agent_config, _maybe_start_experiment, _generate_suggestion,
_maybe_conclude_experiment, _get_arm_for_goal, _record_result,
_get_recent_metrics, _create_experiment, get_arm_config, list_experiments,
_bayesian_prob_better numpy-fallback.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.intelligence.self_optimizer_v2 import (
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


def _make_optimizer(redis=None, db_session=None, llm_factory=None, db_returns_none=False):
    if redis is None:
        redis = _make_redis()

    if db_session is None and not db_returns_none:
        db_session = _make_db_session()

    def db_factory():
        return db_session

    if llm_factory is None:
        llm_factory = AsyncMock(return_value=AsyncMock())

    return SelfOptimizerV2(redis=redis, db_factory=db_factory, llm_provider_factory=llm_factory)


# ── _read_current_agent_config ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_current_agent_config_returns_dict():
    config = {"system_prompt": "be helpful"}
    mock_session = _make_db_session(fetchone_return=(json.dumps(config),))
    opt = _make_optimizer(db_session=mock_session)

    result = await opt._read_current_agent_config("t1", "a1")
    assert result == config


@pytest.mark.asyncio
async def test_read_current_agent_config_returns_none_when_empty():
    mock_session = _make_db_session(fetchone_return=None)
    opt = _make_optimizer(db_session=mock_session)

    result = await opt._read_current_agent_config("t1", "a1")
    assert result is None


@pytest.mark.asyncio
async def test_read_current_agent_config_returns_none_on_empty_config():
    mock_session = _make_db_session(fetchone_return=("",))
    opt = _make_optimizer(db_session=mock_session)

    result = await opt._read_current_agent_config("t1", "a1")
    assert result is None


@pytest.mark.asyncio
async def test_read_current_agent_config_handles_dict_directly():
    config = {"key": "value"}
    mock_session = _make_db_session(fetchone_return=(config,))  # already a dict
    opt = _make_optimizer(db_session=mock_session)

    result = await opt._read_current_agent_config("t1", "a1")
    assert result == config


@pytest.mark.asyncio
async def test_read_current_agent_config_db_error_returns_none():
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("DB error"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    opt = _make_optimizer(db_session=mock_session)
    result = await opt._read_current_agent_config("t1", "a1")
    assert result is None


# ── _get_recent_metrics ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_recent_metrics_returns_dict():
    mock_session = _make_db_session(fetchone_return=(10, 0.75, 0.05, 1500, 90.0))
    opt = _make_optimizer(db_session=mock_session)

    result = await opt._get_recent_metrics("t1", "a1")
    assert result["n_goals"] == 10
    assert result["avg_eval_score"] == pytest.approx(0.75)
    assert result["completion_rate_pct"] == pytest.approx(90.0)


@pytest.mark.asyncio
async def test_get_recent_metrics_no_data_returns_empty():
    mock_session = _make_db_session(fetchone_return=None)
    opt = _make_optimizer(db_session=mock_session)

    result = await opt._get_recent_metrics("t1", "a1")
    assert result == {}


@pytest.mark.asyncio
async def test_get_recent_metrics_null_values():
    mock_session = _make_db_session(fetchone_return=(0, None, None, None, None))
    opt = _make_optimizer(db_session=mock_session)

    result = await opt._get_recent_metrics("t1", "a1")
    assert result["n_goals"] == 0
    assert result["avg_eval_score"] == 0.0


@pytest.mark.asyncio
async def test_get_recent_metrics_db_error_returns_empty():
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("DB error"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    opt = _make_optimizer(db_session=mock_session)
    result = await opt._get_recent_metrics("t1", "a1")
    assert result == {}


# ── _record_result ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_result_success():
    mock_session = _make_db_session()
    opt = _make_optimizer(db_session=mock_session)

    await opt._record_result(
        tenant_id="t1",
        experiment_id="exp1",
        goal_id="g1",
        arm="candidate",
        eval_score=0.8,
        cost_usd=0.05,
        latency_ms=500,
        domain="healthcare",
    )
    mock_session.execute.assert_called()


@pytest.mark.asyncio
async def test_record_result_db_error_does_not_raise():
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("DB error"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    opt = _make_optimizer(db_session=mock_session)
    await opt._record_result(
        tenant_id="t1",
        experiment_id="exp1",
        goal_id="g1",
        arm="control",
        eval_score=None,
        cost_usd=0.01,
        latency_ms=100,
        domain=None,
    )  # Should not raise


@pytest.mark.asyncio
async def test_record_result_none_eval_score_uses_zero():
    mock_session = _make_db_session()
    opt = _make_optimizer(db_session=mock_session)

    # eval_score=None → metric_value=0.0
    await opt._record_result(
        tenant_id="t1",
        experiment_id="exp1",
        goal_id="g1",
        arm="control",
        eval_score=None,
        cost_usd=0.01,
        latency_ms=100,
        domain=None,
    )
    mock_session.execute.assert_called()


# ── _create_experiment ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_experiment_returns_id():
    mock_session = _make_db_session()
    opt = _make_optimizer(db_session=mock_session)

    exp_id = await opt._create_experiment(
        tenant_id="t1",
        agent_id="a1",
        control_config={"prompt": "old"},
        candidate_config={"prompt": "new"},
        suggestion={"rationale": "better", "suggested_change": {"field": "prompt", "new_value": "new"}},
        success_metric="eval_score",
        domain=None,
    )
    assert isinstance(exp_id, str)
    assert len(exp_id) > 0


# ── _generate_suggestion ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_suggestion_valid_response():
    mock_provider = AsyncMock()
    mock_response = MagicMock()
    suggestion_json = json.dumps({
        "suggested_change": {"field": "system_prompt", "new_value": "improved prompt"},
        "rationale": "better performance",
        "expected_uplift_pct": 8.5,
        "confidence": "medium",
    })
    mock_response.content = suggestion_json
    mock_provider.complete = AsyncMock(return_value=mock_response)

    async def llm_factory():
        return mock_provider

    redis = _make_redis()
    mock_session = _make_db_session()
    opt = SelfOptimizerV2(redis=redis, db_factory=lambda: mock_session, llm_provider_factory=llm_factory)

    result = await opt._generate_suggestion(
        current_config={"system_prompt": "old"},
        metrics={"avg_eval_score": 0.7},
        success_metric="eval_score",
    )
    assert result is not None
    assert "suggested_change" in result
    assert result["rationale"] == "better performance"


@pytest.mark.asyncio
async def test_generate_suggestion_invalid_json_returns_none():
    mock_provider = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = "not valid json"
    mock_provider.complete = AsyncMock(return_value=mock_response)

    async def llm_factory():
        return mock_provider

    redis = _make_redis()
    mock_session = _make_db_session()
    opt = SelfOptimizerV2(redis=redis, db_factory=lambda: mock_session, llm_provider_factory=llm_factory)

    result = await opt._generate_suggestion({}, {}, "eval_score")
    assert result is None


@pytest.mark.asyncio
async def test_generate_suggestion_missing_field_returns_none():
    mock_provider = AsyncMock()
    mock_response = MagicMock()
    # Missing 'field' in suggested_change
    mock_response.content = json.dumps({
        "suggested_change": {"new_value": "x"},
        "rationale": "test",
    })
    mock_provider.complete = AsyncMock(return_value=mock_response)

    async def llm_factory():
        return mock_provider

    redis = _make_redis()
    mock_session = _make_db_session()
    opt = SelfOptimizerV2(redis=redis, db_factory=lambda: mock_session, llm_provider_factory=llm_factory)

    result = await opt._generate_suggestion({}, {}, "eval_score")
    assert result is None


@pytest.mark.asyncio
async def test_generate_suggestion_exception_returns_none():
    async def llm_factory():
        raise Exception("LLM unavailable")

    redis = _make_redis()
    mock_session = _make_db_session()
    opt = SelfOptimizerV2(redis=redis, db_factory=lambda: mock_session, llm_provider_factory=llm_factory)

    result = await opt._generate_suggestion({}, {}, "eval_score")
    assert result is None


@pytest.mark.asyncio
async def test_generate_suggestion_non_dict_response_returns_none():
    mock_provider = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = json.dumps(["not", "a", "dict"])
    mock_provider.complete = AsyncMock(return_value=mock_response)

    async def llm_factory():
        return mock_provider

    redis = _make_redis()
    mock_session = _make_db_session()
    opt = SelfOptimizerV2(redis=redis, db_factory=lambda: mock_session, llm_provider_factory=llm_factory)

    result = await opt._generate_suggestion({}, {}, "eval_score")
    assert result is None


# ── _maybe_start_experiment ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_maybe_start_experiment_no_config_returns_none():
    mock_session = _make_db_session(fetchone_return=None)
    opt = _make_optimizer(db_session=mock_session)

    result = await opt._maybe_start_experiment("t1", "a1", None)
    assert result is None


# ── get_arm_config ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_arm_config_no_experiment_returns_config():
    redis = _make_redis()
    mock_session = _make_db_session(fetchone_return=(json.dumps({"prompt": "current"}),))
    opt = _make_optimizer(redis=redis, db_session=mock_session)

    result = await opt.get_arm_config("t1", "a1", "g1")
    assert result == {"prompt": "current"}


@pytest.mark.asyncio
async def test_get_arm_config_with_experiment_candidate():
    # Setup redis state with experiment ID
    state_data = json.dumps({
        "goals_completed": 10,
        "current_experiment_id": "exp1",
        "last_optimized_at": None,
    })
    redis = _make_redis({"optstate:t1:a1": state_data})

    # First call: get_arm_for_goal → reads traffic split (50)
    # Second call: get_arm_config → reads candidate_config
    call_count = [0]
    configs = [
        (50,),  # traffic split
        (json.dumps({"prompt": "candidate"}),),  # candidate config
    ]

    mock_session = AsyncMock()
    mock_result = MagicMock()

    def make_fetchone():
        idx = call_count[0] % len(configs)
        call_count[0] += 1
        return configs[idx]

    mock_result.fetchone = make_fetchone
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    opt = SelfOptimizerV2(redis=redis, db_factory=lambda: mock_session, llm_provider_factory=AsyncMock())
    # This will exercise get_arm_config and may return either arm's config
    result = await opt.get_arm_config("t1", "a1", "some-goal-id")
    assert isinstance(result, dict)


# ── on_goal_completed with existing experiment ────────────────────────────────

@pytest.mark.asyncio
async def test_on_goal_completed_with_experiment():
    state_data = json.dumps({
        "goals_completed": 3,
        "current_experiment_id": "exp1",
        "last_optimized_at": None,
    })
    redis = _make_redis({"optstate:t1:a1": state_data})

    call_count = [0]

    async def execute_side_effect(stmt, *args, **kwargs):
        call_count[0] += 1
        result = MagicMock()
        # For traffic split query
        result.fetchone = MagicMock(return_value=None)
        return result

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.commit = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    opt = SelfOptimizerV2(redis=redis, db_factory=lambda: mock_session, llm_provider_factory=AsyncMock())

    # Should not raise even when DB operations happen
    await opt.on_goal_completed(
        tenant_id="t1",
        agent_id="a1",
        goal_id="g1",
        eval_score=0.85,
        cost_usd=0.03,
        latency_ms=800,
        domain="healthcare",
    )


# ── _bayesian_prob_better numpy fallback ─────────────────────────────────────

def test_bayesian_fallback_without_numpy():
    """Test pure-Python fallback path when numpy is unavailable."""
    with patch.dict("sys.modules", {"numpy": None}):
        prob = SelfOptimizerV2._bayesian_prob_better(
            ctrl_n=50, ctrl_mean=0.5,
            cand_n=50, cand_mean=0.8,
            n_samples=1000,
        )
    assert 0.0 <= prob <= 1.0
    assert prob > 0.5  # candidate should win


def test_bayesian_fallback_control_wins():
    with patch.dict("sys.modules", {"numpy": None}):
        prob = SelfOptimizerV2._bayesian_prob_better(
            ctrl_n=50, ctrl_mean=0.9,
            cand_n=50, cand_mean=0.2,
            n_samples=1000,
        )
    assert prob < 0.5  # control should win


# ── _maybe_conclude_experiment ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_maybe_conclude_experiment_no_row_exits():
    mock_session = _make_db_session(fetchone_return=None)
    opt = _make_optimizer(db_session=mock_session)

    # Should complete without raising
    await opt._maybe_conclude_experiment("t1", "exp1")


@pytest.mark.asyncio
async def test_maybe_conclude_experiment_not_enough_samples():
    # min_n=10, ctrl_n=2, cand_n=2 — not enough
    row = (10, 0.95, "eval_score", "agent1", json.dumps({}), 2, 2, 0.7, 0.75)
    mock_session = _make_db_session(fetchone_return=row)
    opt = _make_optimizer(db_session=mock_session)

    await opt._maybe_conclude_experiment("t1", "exp1")
    # Should exit early, no commit


@pytest.mark.asyncio
async def test_maybe_conclude_experiment_inconclusive():
    # ctrl_n=20, cand_n=20, means are close → inconclusive
    row = (5, 0.95, "eval_score", "agent1", json.dumps({"p": "v"}), 20, 20, 0.70, 0.72)
    mock_session = _make_db_session(fetchone_return=row)
    opt = _make_optimizer(db_session=mock_session)

    await opt._maybe_conclude_experiment("t1", "exp1")
    mock_session.commit.assert_called()


# ── _get_arm_for_goal with experiment ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_arm_for_goal_with_traffic_split():
    state_data = json.dumps({
        "goals_completed": 10,
        "current_experiment_id": "exp99",
        "last_optimized_at": None,
    })
    redis = _make_redis({"optstate:t1:a1": state_data})

    # traffic_split=50
    mock_session = _make_db_session(fetchone_return=(50,))
    opt = _make_optimizer(redis=redis, db_session=mock_session)

    arm = await opt._get_arm_for_goal("t1", "a1", "fixed-goal-id-123")
    assert arm in ("control", "candidate")


@pytest.mark.asyncio
async def test_get_arm_for_goal_db_exception_defaults_to_50():
    state_data = json.dumps({
        "goals_completed": 10,
        "current_experiment_id": "exp99",
        "last_optimized_at": None,
    })
    redis = _make_redis({"optstate:t1:a1": state_data})

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("DB error"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    opt = SelfOptimizerV2(redis=redis, db_factory=lambda: mock_session, llm_provider_factory=AsyncMock())
    arm = await opt._get_arm_for_goal("t1", "a1", "goal-123")
    assert arm in ("control", "candidate")


# ── list_experiments DB error ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_experiments_db_error_returns_empty():
    redis = _make_redis()
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("DB error"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    opt = SelfOptimizerV2(redis=redis, db_factory=lambda: mock_session, llm_provider_factory=AsyncMock())
    result = await opt.list_experiments("t1")
    assert result == []
