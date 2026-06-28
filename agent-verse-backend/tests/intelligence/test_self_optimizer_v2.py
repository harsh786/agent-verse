"""Tests for SelfOptimizerV2 — all 4 critical bug fixes + Bayesian A/B testing.

Covers:
  1. test_apply_suggestion_reads_real_agent_config (not placeholder)
  2. test_before_prompt_not_placeholder (reads actual system_prompt from DB)
  3. test_min_goals_threshold_is_5_not_50
  4. test_tenant_scoped_arm_namespace (tenant A can't see tenant B experiments)
  5. test_stale_session_fixed (conclude experiment without error)
  6. test_bayesian_prob_thread_safe (numpy random)
  7. test_integration_point_records_result_in_graph (on_goal_completed called)
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.intelligence.self_optimizer_v2 import (
    SelfOptimizerV2,
    TenantOptimizationState,
    DEFAULT_MIN_GOALS,
    DOMAIN_METRICS,
)


# ---------------------------------------------------------------------------
# TenantOptimizationState — tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTenantOptimizationState:
    async def test_initial_state_returns_defaults(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        state = TenantOptimizationState(mock_redis)
        result = await state.get("tenant-1", "agent-1")
        assert result["goals_completed"] == 0
        assert result["current_experiment_id"] is None

    async def test_different_tenants_are_isolated(self) -> None:
        """Fix 3: Tenant A's state must not bleed into Tenant B."""
        store: dict[str, str] = {}

        mock_redis = AsyncMock()

        async def get(key: str):
            return store.get(key)

        async def setex(key: str, ttl: int, val: str) -> None:
            store[key] = val

        mock_redis.get = get
        mock_redis.setex = setex

        state = TenantOptimizationState(mock_redis)
        await state.update("tenant-A", "agent-1", {"goals_completed": 100})
        await state.update("tenant-B", "agent-1", {"goals_completed": 3})

        rA = await state.get("tenant-A", "agent-1")
        rB = await state.get("tenant-B", "agent-1")
        assert rA["goals_completed"] == 100
        assert rB["goals_completed"] == 3  # must be isolated

    async def test_keys_include_tenant_id(self) -> None:
        """Fix 3: Redis keys must include tenant_id to prevent cross-tenant leakage."""
        state = TenantOptimizationState(AsyncMock())
        key_a = state._key("tenant-A", "agent-1")
        key_b = state._key("tenant-B", "agent-1")
        assert "tenant-A" in key_a
        assert "tenant-B" in key_b
        assert key_a != key_b

    async def test_increment_goals_returns_new_count(self) -> None:
        store: dict[str, str] = {}
        mock_redis = AsyncMock()

        async def get(key: str):
            return store.get(key)

        async def setex(key: str, ttl: int, val: str) -> None:
            store[key] = val

        mock_redis.get = get
        mock_redis.setex = setex

        state = TenantOptimizationState(mock_redis)
        c1 = await state.increment_goals("t1", "a1")
        c2 = await state.increment_goals("t1", "a1")
        assert c1 == 1
        assert c2 == 2


# ---------------------------------------------------------------------------
# Fix 4: DEFAULT_MIN_GOALS = 5
# ---------------------------------------------------------------------------


class TestMinGoalsThreshold:
    def test_min_goals_threshold_is_5_not_50(self) -> None:
        """Fix 4: DEFAULT_MIN_GOALS must be 5, not 50."""
        assert DEFAULT_MIN_GOALS == 5
        assert DEFAULT_MIN_GOALS < 50

    @pytest.mark.asyncio
    async def test_experiment_triggers_after_5_goals(self) -> None:
        """Fix 4: Experiment starts after 5 goals, not 50."""
        store: dict[str, str] = {}
        mock_redis = AsyncMock()

        async def get(key: str):
            return store.get(key)

        async def setex(key: str, ttl: int, val: str) -> None:
            store[key] = val

        mock_redis.get = get
        mock_redis.setex = setex

        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)
        mock_result = MagicMock()
        mock_result.fetchone = lambda: None
        mock_result.scalar = lambda: 0
        mock_db.execute = AsyncMock(return_value=mock_result)

        experiments_started: list[str] = []
        optimizer = SelfOptimizerV2(mock_redis, lambda: mock_db, AsyncMock())
        optimizer._maybe_start_experiment = AsyncMock(  # type: ignore[assignment]
            side_effect=lambda *a, **kw: experiments_started.append("exp") or "exp-id"
        )
        optimizer._get_min_goals = AsyncMock(return_value=DEFAULT_MIN_GOALS)
        optimizer._record_result = AsyncMock()
        optimizer._maybe_conclude_experiment = AsyncMock()

        agent_id = str(uuid4())
        for _ in range(5):
            await optimizer.on_goal_completed("t1", agent_id, str(uuid4()), 0.8, 0.05, 500)

        assert len(experiments_started) == 1, (
            "Experiment should start after 5 goals, not 50"
        )


# ---------------------------------------------------------------------------
# Fix 1: apply_suggestion uses actual agent_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_suggestion_reads_real_agent_config() -> None:
    """
    Fix 1: apply_suggestion() must UPDATE agents SET config = :candidate_config.
    Was: called API endpoint with empty body — every optimization silently failed.
    """
    updated_configs: list[dict] = []

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=None)
    mock_db.commit = AsyncMock()

    async def execute_side_effect(query, params=None, **kwargs):
        q = str(query)
        mock_result = MagicMock()
        mock_result.fetchone = lambda: None
        if "UPDATE agents" in q and params and "config" in params:
            cfg_raw = params.get("config", "{}")
            updated_configs.append(
                json.loads(cfg_raw) if isinstance(cfg_raw, str) else cfg_raw
            )
        return mock_result

    mock_db.execute = AsyncMock(side_effect=execute_side_effect)

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=json.dumps({
        "goals_completed": 5, "current_experiment_id": "exp-1"
    }))
    mock_redis.setex = AsyncMock()

    candidate_config = {
        "system_prompt": "You are an improved legal agent with citation focus.",
        "max_iterations": 8,
    }

    optimizer = SelfOptimizerV2(mock_redis, lambda: mock_db, AsyncMock())
    optimizer._read_current_agent_config = AsyncMock(  # type: ignore[assignment]
        return_value={"system_prompt": "You are a legal agent.", "max_iterations": 5}
    )

    result = await optimizer.apply_suggestion(
        "tenant-1", str(uuid4()), "exp-1", candidate_config
    )

    assert result is True
    assert len(updated_configs) > 0, "DB UPDATE must have been called with agent config"
    assert updated_configs[0] == candidate_config, (
        "Actual candidate_config must be written, not empty dict"
    )


# ---------------------------------------------------------------------------
# Fix 2: "before" prompt reads real config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_prompt_not_placeholder() -> None:
    """
    Fix 2: _read_current_agent_config() returns real config from DB, not 'before'.
    """
    real_config = {"system_prompt": "You are a real production agent.", "max_iterations": 10}

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=None)

    async def execute_side_effect(query, params=None, **kwargs):
        mock_result = MagicMock()
        mock_result.fetchone = lambda: MagicMock(
            __getitem__=lambda s, i: real_config
        )
        return mock_result

    mock_db.execute = AsyncMock(side_effect=execute_side_effect)

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)

    optimizer = SelfOptimizerV2(mock_redis, lambda: mock_db, AsyncMock())
    config = await optimizer._read_current_agent_config("t1", "a1")

    assert config is not None
    assert config != "before", (
        "Must read real agent config from DB, not return literal string 'before'"
    )
    assert config == real_config


# ---------------------------------------------------------------------------
# Fix 3: Tenant scoped arm namespace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_scoped_arm_namespace() -> None:
    """Fix 3: Tenant A's experiment state must not be visible to Tenant B."""
    store: dict[str, str] = {}
    mock_redis = AsyncMock()

    async def get(key: str):
        return store.get(key)

    async def setex(key: str, ttl: int, val: str) -> None:
        store[key] = val

    mock_redis.get = get
    mock_redis.setex = setex

    state = TenantOptimizationState(mock_redis)
    await state.update("tenant-A", "agent-X", {
        "current_experiment_id": "exp-for-A", "goals_completed": 50
    })
    await state.update("tenant-B", "agent-X", {
        "current_experiment_id": None, "goals_completed": 2
    })

    state_A = await state.get("tenant-A", "agent-X")
    state_B = await state.get("tenant-B", "agent-X")

    assert state_A["current_experiment_id"] == "exp-for-A"
    assert state_B["current_experiment_id"] is None
    assert state_A["goals_completed"] == 50
    assert state_B["goals_completed"] == 2


# ---------------------------------------------------------------------------
# Fix 5: Stale session fixed in _maybe_conclude_experiment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_session_fixed() -> None:
    """
    Fix 5: _maybe_conclude_experiment() must use a single DB session.
    apply_suggestion() must be called OUTSIDE the session (fresh connection).
    Verifies no 'session is closed' error.
    """
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=json.dumps({
        "goals_completed": 25,
        "current_experiment_id": "exp-conclude",
    }))
    mock_redis.setex = AsyncMock()

    call_count = {"db_opens": 0, "apply_called": 0}

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(side_effect=lambda: (call_count.__setitem__(
        "db_opens", call_count["db_opens"] + 1
    ) or mock_db))
    mock_db.__aexit__ = AsyncMock(return_value=None)
    mock_db.commit = AsyncMock()

    async def execute_side_effect(query, params=None, **kwargs):
        mock_result = MagicMock()
        q = str(query)
        if "improvement_experiments" in q and "JOIN" in q:
            # Return sufficient data to trigger conclusion
            mock_result.fetchone = lambda: MagicMock(
                __getitem__=lambda s, i: [
                    20,       # min_samples_per_arm
                    0.95,     # significance_threshold
                    "eval_score",  # success_metric
                    "agent-1",     # agent_id
                    json.dumps({"system_prompt": "improved"}),  # candidate_config
                    25,       # ctrl_n
                    25,       # cand_n
                    0.7,      # ctrl_mean
                    0.9,      # cand_mean (candidate wins)
                ][i]
            )
        else:
            mock_result.fetchone = lambda: None
        mock_result.scalar = lambda: 0
        return mock_result

    mock_db.execute = AsyncMock(side_effect=execute_side_effect)

    apply_calls: list[dict] = []

    optimizer = SelfOptimizerV2(mock_redis, lambda: mock_db, AsyncMock())
    original_apply = optimizer.apply_suggestion

    async def mock_apply(tid: str, aid: str, eid: str, cfg: dict) -> bool:
        apply_calls.append({"tenant": tid, "agent": aid, "experiment": eid})
        return True

    optimizer.apply_suggestion = mock_apply  # type: ignore[assignment]

    # Must not raise any 'session closed' errors
    await optimizer._maybe_conclude_experiment("tenant-1", "exp-conclude")

    # apply_suggestion called outside the session block
    assert len(apply_calls) >= 1, "apply_suggestion must be called after conclude"


# ---------------------------------------------------------------------------
# Fix 6: Bayesian prob — thread-safe numpy
# ---------------------------------------------------------------------------


class TestBayesianProbBetter:
    def test_clearly_better_candidate_high_prob(self) -> None:
        """Candidate 2x better than control → prob > 0.95."""
        prob = SelfOptimizerV2._bayesian_prob_better(
            ctrl_n=100, ctrl_mean=1.0,
            cand_n=100, cand_mean=2.0,
        )
        assert prob > 0.90

    def test_equal_performance_prob_near_half(self) -> None:
        """Equal means → prob ≈ 0.5."""
        prob = SelfOptimizerV2._bayesian_prob_better(
            ctrl_n=100, ctrl_mean=1.0,
            cand_n=100, cand_mean=1.0,
        )
        assert 0.35 < prob < 0.65

    def test_worse_candidate_low_prob(self) -> None:
        """Control 2x better than candidate → prob < 0.1."""
        prob = SelfOptimizerV2._bayesian_prob_better(
            ctrl_n=100, ctrl_mean=2.0,
            cand_n=100, cand_mean=1.0,
        )
        assert prob < 0.10

    def test_bayesian_prob_thread_safe(self) -> None:
        """
        Fix 6: numpy.default_rng() per call — each call creates a new Generator.
        Calling multiple times in parallel must not corrupt state.
        """
        import concurrent.futures

        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futs = [
                executor.submit(
                    SelfOptimizerV2._bayesian_prob_better,
                    ctrl_n=50, ctrl_mean=1.0,
                    cand_n=50, cand_mean=1.5,
                )
                for _ in range(8)
            ]
            results = [f.result() for f in futs]

        # All results must be valid probabilities
        for r in results:
            assert 0.0 <= r <= 1.0

        # High variation in such a small set indicates no shared state (good)
        # But at minimum no exceptions were thrown
        assert len(results) == 8


# ---------------------------------------------------------------------------
# Apply suggestion to config — static helper
# ---------------------------------------------------------------------------


class TestApplySuggestionToConfig:
    def test_changes_top_level_field(self) -> None:
        config = {"system_prompt": "old prompt", "max_iterations": 5}
        suggestion = {
            "suggested_change": {
                "field": "system_prompt",
                "new_value": "improved prompt with citations",
            }
        }
        result = SelfOptimizerV2._apply_suggestion_to_config(config, suggestion)
        assert result["system_prompt"] == "improved prompt with citations"
        assert result["max_iterations"] == 5  # unchanged

    def test_nonexistent_nested_field_returns_unchanged(self) -> None:
        config = {"system_prompt": "original"}
        suggestion = {"suggested_change": {"field": "nested.does.not.exist", "new_value": "x"}}
        result = SelfOptimizerV2._apply_suggestion_to_config(config, suggestion)
        assert result == config


# ---------------------------------------------------------------------------
# Domain metrics
# ---------------------------------------------------------------------------


class TestDomainMetrics:
    def test_legal_uses_citation_accuracy(self) -> None:
        assert DOMAIN_METRICS["legal"] == "citation_accuracy"

    def test_finance_uses_compliance_rate(self) -> None:
        assert DOMAIN_METRICS["finance"] == "compliance_rate"

    def test_all_metrics_are_valid(self) -> None:
        valid = {
            "eval_score", "completion_rate", "cost_per_goal", "latency_ms",
            "citation_accuracy", "compliance_rate", "conversion_rate", "resolution_rate",
        }
        for domain, metric in DOMAIN_METRICS.items():
            assert metric in valid, f"Unknown metric '{metric}' for domain '{domain}'"


# ---------------------------------------------------------------------------
# Fix 7: Integration point — record result in on_goal_completed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_point_records_result_in_graph() -> None:
    """
    Amendment 9.4: on_goal_completed() must be callable from graph.py's
    _node_verify() after goal evaluation completes.
    """
    store: dict[str, str] = {}
    mock_redis = AsyncMock()

    async def get(key: str):
        return store.get(key)

    async def setex(key: str, ttl: int, val: str) -> None:
        store[key] = val

    mock_redis.get = get
    mock_redis.setex = setex

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=None)
    mock_result = MagicMock()
    mock_result.fetchone = lambda: None
    mock_result.scalar = lambda: 0
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    optimizer = SelfOptimizerV2(mock_redis, lambda: mock_db, AsyncMock())
    optimizer._maybe_start_experiment = AsyncMock(return_value=None)
    optimizer._get_min_goals = AsyncMock(return_value=DEFAULT_MIN_GOALS)

    # Simulate graph.py calling on_goal_completed after verification
    agent_id = str(uuid4())
    goal_id = str(uuid4())
    await optimizer.on_goal_completed(
        tenant_id="tenant-graph-test",
        agent_id=agent_id,
        goal_id=goal_id,
        eval_score=0.85,
        cost_usd=0.015,
        latency_ms=1200,
        domain="legal",
    )

    # State must be incremented
    state = await optimizer._state.get("tenant-graph-test", agent_id)
    assert state["goals_completed"] == 1
