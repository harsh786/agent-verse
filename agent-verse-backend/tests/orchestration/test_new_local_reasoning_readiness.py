from __future__ import annotations

import pytest

from app.orchestration.strategy_readiness import DependencyProbeResult, ReadinessEvaluator
from app.orchestration.strategy_registry import LOCAL_REASONING_LIMITS, build_default_registry


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_id", tuple(LOCAL_REASONING_LIMITS))
async def test_local_reasoning_dependencies_fail_closed(strategy_id: str) -> None:
    capability = build_default_registry().get(strategy_id)
    assert capability is not None
    decision = await ReadinessEvaluator().evaluate(capability, production=True)
    assert not decision.ready
    assert decision.blocking_reasons


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_id", tuple(LOCAL_REASONING_LIMITS))
async def test_local_reasoning_is_ready_only_when_every_required_probe_passes(
    strategy_id: str,
) -> None:
    capability = build_default_registry().get(strategy_id)
    assert capability is not None
    evaluator = ReadinessEvaluator()
    for dependency_id in capability.readiness_requirements:
        if dependency_id != "registry_contract":
            evaluator.register(dependency_id, DependencyProbeResult.ready)
    decision = await evaluator.evaluate(capability, production=True)
    assert decision.ready
    assert not decision.blocking_reasons


@pytest.mark.asyncio
async def test_tool_strategies_require_governed_dispatcher() -> None:
    capability = build_default_registry().get("rewoo")
    assert capability is not None
    evaluator = ReadinessEvaluator()
    for dependency_id in capability.readiness_requirements:
        if dependency_id not in {"registry_contract", "governed_tool_dispatcher"}:
            evaluator.register(dependency_id, DependencyProbeResult.ready)
    decision = await evaluator.evaluate(capability, production=True)
    assert not decision.ready
    assert "missing_probe:governed_tool_dispatcher" in decision.blocking_reasons
