from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.orchestration.strategy_readiness import (
    DependencyProbeResult,
    ReadinessEvaluator,
)
from app.orchestration.strategy_registry import (
    StrategyCapability,
    StrategyCategory,
    StrategyRegistry,
    StrategyState,
    build_default_registry,
)


@pytest.mark.asyncio
async def test_static_evidence_does_not_replace_operational_probe() -> None:
    capability = build_default_registry().get("react")
    assert capability is not None
    evaluator = ReadinessEvaluator(cache_ttl=timedelta(minutes=1))

    decision = await evaluator.evaluate(capability, production=True)

    assert not decision.ready
    assert "missing_probe:strategy_runner" in decision.blocking_reasons


@pytest.mark.asyncio
async def test_live_probes_distinguish_blocking_and_degraded_dependencies() -> None:
    capability = build_default_registry().get("react")
    assert capability is not None
    evaluator = ReadinessEvaluator(cache_ttl=timedelta(minutes=1))
    evaluator.register("strategy_runner", lambda: DependencyProbeResult.ready())
    evaluator.register("optional_metrics", lambda: DependencyProbeResult.degraded("lagging"))

    decision = await evaluator.evaluate(
        capability,
        optional_dependencies=("optional_metrics",),
        production=True,
    )

    assert decision.ready
    assert decision.degraded_reasons == ("optional_metrics:lagging",)


@pytest.mark.asyncio
async def test_probe_exceptions_and_timeouts_fail_closed_in_production() -> None:
    capability = build_default_registry().get("react")
    assert capability is not None
    evaluator = ReadinessEvaluator(
        probe_timeout=0.01,
        cache_ttl=timedelta(minutes=1),
    )

    async def slow() -> DependencyProbeResult:
        await asyncio.sleep(1)
        return DependencyProbeResult.ready()

    evaluator.register("strategy_runner", slow)
    decision = await evaluator.evaluate(capability, production=True)

    assert not decision.ready
    assert decision.blocking_reasons == ("strategy_runner:probe_timeout",)


@pytest.mark.asyncio
async def test_readiness_cache_expires_and_reprobes() -> None:
    capability = build_default_registry().get("react")
    assert capability is not None
    now = datetime(2026, 8, 10, tzinfo=UTC)
    calls = 0

    def probe() -> DependencyProbeResult:
        nonlocal calls
        calls += 1
        return DependencyProbeResult.ready()

    evaluator = ReadinessEvaluator(
        cache_ttl=timedelta(seconds=5),
        clock=lambda: now,
    )
    evaluator.register("strategy_runner", probe)
    await evaluator.evaluate(capability, production=True)
    await evaluator.evaluate(capability, production=True)
    evaluator._clock = lambda: now + timedelta(seconds=6)
    await evaluator.evaluate(capability, production=True)

    assert calls == 2


@pytest.mark.asyncio
async def test_disabled_strategy_is_never_ready() -> None:
    capability = StrategyCapability(
        strategy_id="disabled-test",
        category=StrategyCategory.AGENT,
        state=StrategyState.DISABLED,
    )
    capability = StrategyRegistry([capability], aliases=()).get("disabled-test")
    assert capability is not None
    evaluator = ReadinessEvaluator()

    decision = await evaluator.evaluate(capability, production=True)

    assert not decision.ready
    assert decision.blocking_reasons == ("strategy_disabled",)
