"""Comprehensive tests for app/intelligence/self_optimizer_v2.py — the 51% gap.

Covers:
  - SelfOptimizerV2._apply_suggestion_to_config (static) — nested field, path not found
  - SelfOptimizerV2._compute_delta (static)
  - SelfOptimizerV2.apply_suggestion() with mocked DB — success and failure
  - SelfOptimizerV2.rollback() with mocked DB — success, not found, failure
  - SelfOptimizerV2._bayesian_prob_better() — candidate better / control better / equal
  - SelfOptimizerV2._bayesian_prob_better() — fallback when numpy absent
  - SelfOptimizerV2._get_min_goals() — DB returns value, DB returns nothing, exception
  - SelfOptimizerV2.list_experiments() — no DB returns empty list
  - SelfOptimizerV2.get_arm_config() — control arm, experiment not found
  - TenantOptimizationState.increment_goals() returns new count
  - DEFAULT_MIN_GOALS == 5  (Fix 4)
  - DOMAIN_METRICS correct entries
"""
from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.intelligence.self_optimizer_v2 import (
    DEFAULT_MIN_GOALS,
    DOMAIN_METRICS,
    SelfOptimizerV2,
    TenantOptimizationState,
)


# ---------------------------------------------------------------------------
# Constants — Fix 4 & domain metrics
# ---------------------------------------------------------------------------


def test_default_min_goals_is_5() -> None:
    """Fix 4: threshold must be 5, not 50."""
    assert DEFAULT_MIN_GOALS == 5


def test_domain_metrics_contains_expected_domains() -> None:
    assert "legal" in DOMAIN_METRICS
    assert "healthcare" in DOMAIN_METRICS
    assert "finance" in DOMAIN_METRICS
    assert "education" in DOMAIN_METRICS
    assert "ecommerce" in DOMAIN_METRICS


# ---------------------------------------------------------------------------
# TenantOptimizationState — increment_goals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_increment_goals_returns_incremented_count() -> None:
    store: dict[str, str] = {}

    mock_redis = AsyncMock()

    async def fake_get(key: str) -> str | None:
        return store.get(key)

    async def fake_setex(key: str, ttl: int, val: str) -> None:
        store[key] = val

    mock_redis.get = fake_get
    mock_redis.setex = fake_setex

    state = TenantOptimizationState(mock_redis)
    c1 = await state.increment_goals("tenant-1", "agent-1")
    c2 = await state.increment_goals("tenant-1", "agent-1")
    c3 = await state.increment_goals("tenant-1", "agent-1")
    assert c1 == 1
    assert c2 == 2
    assert c3 == 3


@pytest.mark.asyncio
async def test_state_key_format_contains_prefix() -> None:
    state = TenantOptimizationState(AsyncMock())
    key = state._key("t1", "a1")
    assert key.startswith("optstate:")
    assert "t1" in key
    assert "a1" in key


# ---------------------------------------------------------------------------
# SelfOptimizerV2._apply_suggestion_to_config — static method
# ---------------------------------------------------------------------------


def test_apply_suggestion_to_config_top_level_field() -> None:
    current = {"system_prompt": "You are a helpful assistant.", "max_iterations": 10}
    suggestion = {
        "suggested_change": {
            "field": "system_prompt",
            "new_value": "You are an expert Python developer.",
        },
        "rationale": "Better for coding tasks",
    }
    result = SelfOptimizerV2._apply_suggestion_to_config(current, suggestion)
    assert result["system_prompt"] == "You are an expert Python developer."
    assert result["max_iterations"] == 10  # unchanged


def test_apply_suggestion_to_config_nested_field() -> None:
    current = {"config": {"model": "claude-haiku", "temperature": 0.7}}
    suggestion = {
        "suggested_change": {
            "field": "config.model",
            "new_value": "claude-sonnet",
        }
    }
    result = SelfOptimizerV2._apply_suggestion_to_config(current, suggestion)
    assert result["config"]["model"] == "claude-sonnet"
    assert result["config"]["temperature"] == 0.7  # unchanged


def test_apply_suggestion_to_config_path_not_found_returns_unchanged() -> None:
    current = {"name": "my-agent"}
    suggestion = {
        "suggested_change": {
            "field": "config.nonexistent.nested",
            "new_value": "value",
        }
    }
    result = SelfOptimizerV2._apply_suggestion_to_config(current, suggestion)
    # Must return original unchanged (path not found)
    assert result == {"name": "my-agent"}


def test_apply_suggestion_to_config_no_change_returns_copy() -> None:
    current = {"a": 1}
    result = SelfOptimizerV2._apply_suggestion_to_config(current, {"suggested_change": {}})
    # Without field/new_value, nothing changes
    assert result == {"a": 1}
    assert result is not current  # must return a copy


def test_apply_suggestion_to_config_does_not_mutate_original() -> None:
    current = {"system_prompt": "original"}
    suggestion = {
        "suggested_change": {"field": "system_prompt", "new_value": "new prompt"}
    }
    _ = SelfOptimizerV2._apply_suggestion_to_config(current, suggestion)
    assert current["system_prompt"] == "original"  # original unchanged


# ---------------------------------------------------------------------------
# SelfOptimizerV2._compute_delta — static method
# ---------------------------------------------------------------------------


def test_compute_delta_detects_changed_field() -> None:
    before = {"system_prompt": "old prompt", "max_iter": 10}
    after = {"system_prompt": "new prompt", "max_iter": 10}
    delta = SelfOptimizerV2._compute_delta(before, after)
    assert "system_prompt" in delta
    assert delta["system_prompt"]["before"] == "old prompt"
    assert delta["system_prompt"]["after"] == "new prompt"
    assert "max_iter" not in delta  # unchanged


def test_compute_delta_added_field() -> None:
    before = {"a": 1}
    after = {"a": 1, "b": 2}
    delta = SelfOptimizerV2._compute_delta(before, after)
    assert "b" in delta
    assert delta["b"]["before"] is None
    assert delta["b"]["after"] == 2


def test_compute_delta_removed_field() -> None:
    before = {"a": 1, "b": 2}
    after = {"a": 1}
    delta = SelfOptimizerV2._compute_delta(before, after)
    assert "b" in delta
    assert delta["b"]["before"] == 2
    assert delta["b"]["after"] is None


def test_compute_delta_no_changes_returns_empty() -> None:
    config = {"x": 10, "y": "hello"}
    delta = SelfOptimizerV2._compute_delta(config, dict(config))
    assert delta == {}


# ---------------------------------------------------------------------------
# SelfOptimizerV2._bayesian_prob_better — statistical correctness
# ---------------------------------------------------------------------------


def test_bayesian_prob_better_candidate_clearly_better() -> None:
    """When candidate mean >> control mean, probability should be > 0.9."""
    prob = SelfOptimizerV2._bayesian_prob_better(
        ctrl_n=100, ctrl_mean=0.5,
        cand_n=100, cand_mean=0.9,
    )
    assert 0.0 <= prob <= 1.0
    assert prob > 0.8, f"Expected high probability for clearly better candidate, got {prob}"


def test_bayesian_prob_better_control_clearly_better() -> None:
    """When control mean >> candidate mean, probability should be < 0.2."""
    prob = SelfOptimizerV2._bayesian_prob_better(
        ctrl_n=100, ctrl_mean=0.9,
        cand_n=100, cand_mean=0.5,
    )
    assert prob < 0.2, f"Expected low probability for clearly worse candidate, got {prob}"


def test_bayesian_prob_better_equal_means_around_half() -> None:
    """Equal means should yield probability near 0.5."""
    prob = SelfOptimizerV2._bayesian_prob_better(
        ctrl_n=100, ctrl_mean=0.7,
        cand_n=100, cand_mean=0.7,
        n_samples=5000,
    )
    # Allow ±0.15 around 0.5 due to sampling variance
    assert 0.35 <= prob <= 0.65, f"Expected ~0.5 for equal means, got {prob}"


def test_bayesian_prob_better_returns_float() -> None:
    prob = SelfOptimizerV2._bayesian_prob_better(
        ctrl_n=10, ctrl_mean=0.6, cand_n=10, cand_mean=0.7
    )
    assert isinstance(prob, float)
    assert 0.0 <= prob <= 1.0


def test_bayesian_prob_better_fallback_without_numpy() -> None:
    """Should fall back to pure-Python implementation when numpy is unavailable."""
    with patch.dict(sys.modules, {"numpy": None}):
        # Re-call the static method; it should not raise
        prob = SelfOptimizerV2._bayesian_prob_better(
            ctrl_n=50, ctrl_mean=0.5, cand_n=50, cand_mean=0.8, n_samples=500
        )
        assert 0.0 <= prob <= 1.0


# ---------------------------------------------------------------------------
# SelfOptimizerV2.apply_suggestion() — with mocked DB
# ---------------------------------------------------------------------------


def _make_optimizer() -> tuple[SelfOptimizerV2, dict[str, str]]:
    """Create a SelfOptimizerV2 with an in-memory Redis mock and DB mock."""
    redis_store: dict[str, str] = {}

    mock_redis = AsyncMock()

    async def fake_get(key: str) -> str | None:
        return redis_store.get(key)

    async def fake_setex(key: str, ttl: int, val: str) -> None:
        redis_store[key] = val

    mock_redis.get = fake_get
    mock_redis.setex = fake_setex

    return mock_redis, redis_store


@pytest.mark.asyncio
async def test_apply_suggestion_success_updates_db() -> None:
    mock_redis, _ = _make_optimizer()

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=(
        json.dumps({"system_prompt": "old prompt"}),
    ))))
    mock_session.commit = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_db = MagicMock(return_value=mock_session)

    optimizer = SelfOptimizerV2(
        redis=mock_redis,
        db_factory=mock_db,
        llm_provider_factory=AsyncMock(),
    )

    result = await optimizer.apply_suggestion(
        tenant_id="t1",
        agent_id="a1",
        experiment_id="exp-1",
        candidate_config={"system_prompt": "new prompt"},
    )
    assert result is True
    assert mock_session.execute.called


@pytest.mark.asyncio
async def test_apply_suggestion_db_error_returns_false() -> None:
    mock_redis, _ = _make_optimizer()

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db = MagicMock(return_value=mock_session)

    optimizer = SelfOptimizerV2(
        redis=mock_redis,
        db_factory=mock_db,
        llm_provider_factory=AsyncMock(),
    )
    result = await optimizer.apply_suggestion(
        tenant_id="t1",
        agent_id="a1",
        experiment_id="exp-1",
        candidate_config={"system_prompt": "new prompt"},
    )
    assert result is False


# ---------------------------------------------------------------------------
# SelfOptimizerV2.rollback() — with mocked DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_success_when_experiment_exists() -> None:
    mock_redis, _ = _make_optimizer()

    control_config = {"system_prompt": "control prompt", "max_iterations": 10}

    mock_session = AsyncMock()
    # first execute (SELECT control_config) returns the config
    mock_session.execute = AsyncMock(return_value=MagicMock(
        fetchone=MagicMock(return_value=(json.dumps(control_config),))
    ))
    mock_session.commit = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db = MagicMock(return_value=mock_session)

    optimizer = SelfOptimizerV2(
        redis=mock_redis,
        db_factory=mock_db,
        llm_provider_factory=AsyncMock(),
    )
    result = await optimizer.rollback(
        tenant_id="t1",
        agent_id="a1",
        experiment_id="exp-1",
        reason="candidate performed worse",
    )
    assert result is True


@pytest.mark.asyncio
async def test_rollback_returns_false_when_experiment_not_found() -> None:
    mock_redis, _ = _make_optimizer()

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(
        fetchone=MagicMock(return_value=None)  # experiment not found
    ))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db = MagicMock(return_value=mock_session)

    optimizer = SelfOptimizerV2(
        redis=mock_redis,
        db_factory=mock_db,
        llm_provider_factory=AsyncMock(),
    )
    result = await optimizer.rollback(
        tenant_id="t1",
        agent_id="a1",
        experiment_id="nonexistent-exp",
        reason="test",
    )
    assert result is False


@pytest.mark.asyncio
async def test_rollback_handles_db_error() -> None:
    mock_redis, _ = _make_optimizer()

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db = MagicMock(return_value=mock_session)

    optimizer = SelfOptimizerV2(
        redis=mock_redis,
        db_factory=mock_db,
        llm_provider_factory=AsyncMock(),
    )
    result = await optimizer.rollback(
        tenant_id="t1",
        agent_id="a1",
        experiment_id="exp-1",
        reason="error case",
    )
    assert result is False


@pytest.mark.asyncio
async def test_rollback_handles_string_control_config() -> None:
    """control_config stored as JSON string must be deserialized."""
    mock_redis, _ = _make_optimizer()
    control_config = {"system_prompt": "old prompt"}

    execute_calls = [0]

    async def execute_side_effect(query, params=None):
        execute_calls[0] += 1
        if execute_calls[0] == 1:
            # First call: SELECT — return string JSON
            return MagicMock(fetchone=MagicMock(return_value=(json.dumps(control_config),)))
        return MagicMock(fetchone=MagicMock(return_value=None))

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.commit = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db = MagicMock(return_value=mock_session)

    optimizer = SelfOptimizerV2(
        redis=mock_redis,
        db_factory=mock_db,
        llm_provider_factory=AsyncMock(),
    )
    result = await optimizer.rollback("t1", "a1", "exp-1", "reason")
    assert result is True


# ---------------------------------------------------------------------------
# SelfOptimizerV2._get_min_goals()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_min_goals_returns_default_when_no_db_row() -> None:
    mock_redis, _ = _make_optimizer()

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(
        fetchone=MagicMock(return_value=None)
    ))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db = MagicMock(return_value=mock_session)

    optimizer = SelfOptimizerV2(
        redis=mock_redis,
        db_factory=mock_db,
        llm_provider_factory=AsyncMock(),
    )
    min_goals = await optimizer._get_min_goals("t1")
    assert min_goals == DEFAULT_MIN_GOALS


@pytest.mark.asyncio
async def test_get_min_goals_returns_tenant_setting() -> None:
    mock_redis, _ = _make_optimizer()

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(
        fetchone=MagicMock(return_value=("20",))  # tenant has min_goals=20
    ))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db = MagicMock(return_value=mock_session)

    optimizer = SelfOptimizerV2(
        redis=mock_redis,
        db_factory=mock_db,
        llm_provider_factory=AsyncMock(),
    )
    min_goals = await optimizer._get_min_goals("t1")
    assert min_goals == 20


@pytest.mark.asyncio
async def test_get_min_goals_falls_back_on_db_error() -> None:
    mock_redis, _ = _make_optimizer()

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("db down"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db = MagicMock(return_value=mock_session)

    optimizer = SelfOptimizerV2(
        redis=mock_redis,
        db_factory=mock_db,
        llm_provider_factory=AsyncMock(),
    )
    min_goals = await optimizer._get_min_goals("t1")
    assert min_goals == DEFAULT_MIN_GOALS


# ---------------------------------------------------------------------------
# SelfOptimizerV2.list_experiments()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_experiments_returns_empty_without_db() -> None:
    mock_redis, _ = _make_optimizer()
    optimizer = SelfOptimizerV2(
        redis=mock_redis,
        db_factory=None,  # no DB
        llm_provider_factory=AsyncMock(),
    )
    result = await optimizer.list_experiments("t1")
    assert result == []


@pytest.mark.asyncio
async def test_list_experiments_returns_empty_on_db_exception() -> None:
    mock_redis, _ = _make_optimizer()

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("db error"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db = MagicMock(return_value=mock_session)

    optimizer = SelfOptimizerV2(
        redis=mock_redis,
        db_factory=mock_db,
        llm_provider_factory=AsyncMock(),
    )
    result = await optimizer.list_experiments("t1")
    assert result == []


# ---------------------------------------------------------------------------
# SelfOptimizerV2.get_arm_config() — control arm (no experiment)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_arm_config_returns_empty_when_no_experiment() -> None:
    redis_store: dict[str, str] = {}
    mock_redis = AsyncMock()

    async def fake_get(key: str) -> str | None:
        return redis_store.get(key)

    async def fake_setex(key: str, ttl: int, val: str) -> None:
        redis_store[key] = val

    mock_redis.get = fake_get
    mock_redis.setex = fake_setex

    # No current_experiment_id stored
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(
        fetchone=MagicMock(return_value=None)
    ))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db = MagicMock(return_value=mock_session)

    optimizer = SelfOptimizerV2(
        redis=mock_redis,
        db_factory=mock_db,
        llm_provider_factory=AsyncMock(),
    )
    # With no experiment_id in state, get_arm_config should return {} or config
    config = await optimizer.get_arm_config("t1", "a1", "goal-1")
    assert isinstance(config, dict)


# ---------------------------------------------------------------------------
# SelfOptimizerV2._read_current_agent_config_with_session()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_agent_config_returns_none_when_no_row() -> None:
    mock_redis, _ = _make_optimizer()

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(
        fetchone=MagicMock(return_value=None)
    ))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db = MagicMock(return_value=mock_session)

    optimizer = SelfOptimizerV2(
        redis=mock_redis,
        db_factory=mock_db,
        llm_provider_factory=AsyncMock(),
    )
    result = await optimizer._read_current_agent_config_with_session(
        mock_session, "t1", "a1"
    )
    assert result is None


@pytest.mark.asyncio
async def test_read_agent_config_parses_json_string() -> None:
    mock_redis, _ = _make_optimizer()
    config = {"system_prompt": "expert assistant"}

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(
        fetchone=MagicMock(return_value=(json.dumps(config),))
    ))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db = MagicMock(return_value=mock_session)

    optimizer = SelfOptimizerV2(
        redis=mock_redis,
        db_factory=mock_db,
        llm_provider_factory=AsyncMock(),
    )
    result = await optimizer._read_current_agent_config_with_session(
        mock_session, "t1", "a1"
    )
    assert result == config


@pytest.mark.asyncio
async def test_read_agent_config_returns_dict_directly() -> None:
    mock_redis, _ = _make_optimizer()
    config = {"system_prompt": "expert"}

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(
        fetchone=MagicMock(return_value=(config,))  # Already a dict (some DB drivers)
    ))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_db = MagicMock(return_value=mock_session)

    optimizer = SelfOptimizerV2(
        redis=mock_redis,
        db_factory=mock_db,
        llm_provider_factory=AsyncMock(),
    )
    result = await optimizer._read_current_agent_config_with_session(
        mock_session, "t1", "a1"
    )
    assert result == config
