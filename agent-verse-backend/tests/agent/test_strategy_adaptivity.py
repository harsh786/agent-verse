"""P5 adaptivity — observed reliability downgrades the static strategy."""

from __future__ import annotations

import pytest

import random

from app.agent.execution_strategy import (
    ExecutionStrategy,
    ModelCapabilityProfile,
    PlanMode,
    ToolMode,
)
from app.agent.strategy_adaptivity import (
    RedisCapabilityTracker,
    apply_exploration,
    refine_strategy,
)


def _structured_parallel() -> ExecutionStrategy:
    return ExecutionStrategy(plan_mode=PlanMode.STRUCTURED, tool_mode=ToolMode.PARALLEL)


def _sequential_single() -> ExecutionStrategy:
    return ExecutionStrategy(plan_mode=PlanMode.SEQUENTIAL, tool_mode=ToolMode.SINGLE)


# ── pure refine_strategy — bidirectional ─────────────────────────────────────


def test_low_structured_rate_downgrades_plan_mode():
    s = refine_strategy(_structured_parallel(), structured_ok_rate=0.3)
    assert s.plan_mode == PlanMode.SEQUENTIAL
    assert s.tool_mode == ToolMode.PARALLEL  # unaffected


def test_low_parallel_rate_downgrades_tool_mode():
    s = refine_strategy(_structured_parallel(), parallel_ok_rate=0.2)
    assert s.tool_mode == ToolMode.SINGLE
    assert s.plan_mode == PlanMode.STRUCTURED


def test_healthy_rates_keep_strategy():
    s = refine_strategy(_structured_parallel(), structured_ok_rate=0.95, parallel_ok_rate=0.9)
    assert s.plan_mode == PlanMode.STRUCTURED
    assert s.tool_mode == ToolMode.PARALLEL


def test_unknown_rates_are_noop():
    s = refine_strategy(_structured_parallel())
    assert s.plan_mode == PlanMode.STRUCTURED
    assert s.tool_mode == ToolMode.PARALLEL


def test_high_rate_upgrades_from_seed():
    """Observation wins over the seed: a model that reliably emits structured
    plans / parallel tools is promoted even though the seed defaulted to safe."""
    s = refine_strategy(_sequential_single(), structured_ok_rate=0.9, parallel_ok_rate=0.85)
    assert s.plan_mode == PlanMode.STRUCTURED
    assert s.tool_mode == ToolMode.PARALLEL


def test_hysteresis_band_keeps_current():
    # 0.7 is between downgrade (0.6) and upgrade (0.8) -> no change either way
    assert refine_strategy(_sequential_single(), structured_ok_rate=0.7).plan_mode == (
        PlanMode.SEQUENTIAL
    )
    assert refine_strategy(_structured_parallel(), structured_ok_rate=0.7).plan_mode == (
        PlanMode.STRUCTURED
    )


# ── cold-start exploration ───────────────────────────────────────────────────


def _profile(model_id: str, json_reliability: str = "medium") -> ModelCapabilityProfile:
    return ModelCapabilityProfile(model_id=model_id, json_reliability=json_reliability)


class _AlwaysExplore(random.Random):
    def random(self) -> float:
        return 0.0  # below any positive probability


class _NeverExplore(random.Random):
    def random(self) -> float:
        return 0.99


def test_unknown_model_is_probed_during_cold_start():
    s = apply_exploration(
        _sequential_single(),
        planner_profile=_profile("brand-new-model"),
        executor_profile=_profile("brand-new-model"),
        structured_rate_known=False,
        parallel_rate_known=False,
        rng=_AlwaysExplore(),
    )
    assert s.plan_mode == PlanMode.STRUCTURED
    assert s.tool_mode == ToolMode.PARALLEL


def test_no_exploration_once_rate_is_known():
    s = apply_exploration(
        _sequential_single(),
        planner_profile=_profile("brand-new-model"),
        executor_profile=_profile("brand-new-model"),
        structured_rate_known=True,
        parallel_rate_known=True,
        rng=_AlwaysExplore(),
    )
    assert s.plan_mode == PlanMode.SEQUENTIAL
    assert s.tool_mode == ToolMode.SINGLE


def test_seeded_incapable_low_reliability_not_probed():
    # a seeded model with low json reliability is confidently incapable -> no probe
    s = apply_exploration(
        _sequential_single(),
        planner_profile=ModelCapabilityProfile(model_id="gpt-oss", json_reliability="low"),
        executor_profile=ModelCapabilityProfile(model_id="gpt-oss", json_reliability="low"),
        structured_rate_known=False,
        parallel_rate_known=False,
        rng=_AlwaysExplore(),
    )
    assert s.plan_mode == PlanMode.SEQUENTIAL


# ── RedisCapabilityTracker (fake redis) ──────────────────────────────────────


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, int]] = {}

    async def hincrby(self, key, field, amount):
        self.store.setdefault(key, {})
        self.store[key][field] = self.store[key].get(field, 0) + amount

    async def expire(self, key, ttl):
        return True

    async def hgetall(self, key):
        return dict(self.store.get(key, {}))


@pytest.mark.asyncio
async def test_tracker_records_and_reports_rate():
    tr = RedisCapabilityTracker(_FakeRedis())
    # 3 ok, 2 bad = 5 samples -> 0.6
    for ok in (True, True, True, False, False):
        await tr.record("m1", ok=ok, tenant_id="t1", kind="structured")
    rate = await tr.rate("m1", tenant_id="t1", kind="structured")
    assert rate == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_tracker_unknown_below_min_observations():
    tr = RedisCapabilityTracker(_FakeRedis())
    for _ in range(2):  # below _MIN_OBSERVATIONS
        await tr.record("m2", ok=True, tenant_id="t1")
    assert await tr.rate("m2", tenant_id="t1") is None


@pytest.mark.asyncio
async def test_tracker_none_for_missing_key():
    tr = RedisCapabilityTracker(_FakeRedis())
    assert await tr.rate("never-seen") is None


@pytest.mark.asyncio
async def test_tracker_redis_error_is_safe():
    class _Boom:
        async def hgetall(self, key):
            raise RuntimeError("down")

    tr = RedisCapabilityTracker(_Boom())
    assert await tr.rate("m") is None
