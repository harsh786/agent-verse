from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.coordination.moa.adapter import MoARuntime
from app.coordination.moa.layer_planner import LayerPlanner
from app.coordination.moa.models import ModelCandidate
from app.coordination.moa.repository import InMemoryMoARepository
from app.coordination.patterns.common import InMemoryPatternCheckpointStore


def _candidate(identifier: str) -> ModelCandidate:
    return ModelCandidate(
        candidate_id=identifier,
        provider_id=identifier,
        model_family=identifier,
        deployment_id=identifier,
        region="in",
        failure_domain=identifier,
        healthy=True,
        context_limit=10_000,
        estimated_cost_usd=0.01,
        estimated_latency_ms=10,
        capabilities=frozenset({"reasoning"}),
    )


@pytest.mark.asyncio
async def test_two_layer_moa_persists_provenance_and_is_restart_safe() -> None:
    candidates = (_candidate("a"), _candidate("b"), _candidate("aggregate"))
    plan = LayerPlanner().plan(
        candidates=candidates,
        layers=2,
        fan_out=2,
        quorum=2,
        aggregator_id="aggregate",
        maximum_cost_usd=1,
        deadline=datetime.now(UTC) + timedelta(minutes=1),
        replacement_waves=0,
    )
    calls: list[tuple[int, str]] = []
    runtime = MoARuntime(
        repository=InMemoryMoARepository(),
        checkpoint_store=InMemoryPatternCheckpointStore(),
    )

    async def propose(layer, candidate, _context):
        calls.append((layer, candidate.deployment_id))
        return {
            "safe_excerpt": f"answer {layer} {candidate.deployment_id}",
            "proposal_reference": f"artifact://{layer}/{candidate.deployment_id}",
            "evidence_references": (f"evidence://{layer}/{candidate.deployment_id}",),
            "tokens": 10,
            "cost_usd": 0.01,
            "quality_score": 8000,
        }

    kwargs = {
        "tenant_id": "tenant",
        "session_id": "session",
        "execution_id": "execution",
        "objective": "solve",
        "plan": plan,
        "candidates": candidates,
        "propose": propose,
        "aggregate": lambda layer, _input: {
            "aggregate_reference": f"artifact://aggregate/{layer}",
            "safe_output": f"aggregate {layer}",
        },
        "maximum_cost_usd": 1,
    }
    result = await runtime.execute(**kwargs)
    resumed = await runtime.execute(**kwargs)
    assert result == resumed and result.phase == "completed"
    assert result.safe_output == "aggregate 1"
    assert calls == [(0, "a"), (0, "b"), (1, "a"), (1, "b")]


@pytest.mark.asyncio
async def test_moa_fails_without_quorum_and_can_degrade_deterministically() -> None:
    candidates = (_candidate("a"), _candidate("b"), _candidate("aggregate"))
    plan = LayerPlanner().plan(
        candidates=candidates,
        layers=1,
        fan_out=2,
        quorum=2,
        aggregator_id="aggregate",
        maximum_cost_usd=1,
        deadline=datetime.now(UTC) + timedelta(minutes=1),
        replacement_waves=0,
    )

    async def partial(_layer, candidate, _context):
        if candidate.deployment_id == "b":
            raise RuntimeError("provider down")
        return {
            "safe_excerpt": "best",
            "proposal_reference": "artifact://best",
            "quality_score": 9000,
        }

    runtime = MoARuntime(
        repository=InMemoryMoARepository(),
        checkpoint_store=InMemoryPatternCheckpointStore(),
    )
    common = {
        "tenant_id": "tenant",
        "session_id": "session",
        "objective": "solve",
        "plan": plan,
        "candidates": candidates,
        "propose": partial,
        "aggregate": lambda *_: pytest.fail("quorum failed"),
        "maximum_cost_usd": 1,
    }
    failed = await runtime.execute(**common, execution_id="fail", allow_degraded=False)
    degraded = await runtime.execute(**common, execution_id="degrade", allow_degraded=True)
    assert failed.phase == "failed" and failed.terminal_reason == "quorum_failed"
    assert degraded.phase == "completed" and degraded.degraded
    assert degraded.safe_output == "best"
