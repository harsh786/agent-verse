from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.coordination.moa.layer_planner import LayerPlanner
from app.coordination.moa.models import ModelCandidate


def _candidate(identifier: str, *, cost: float = 0.1, latency: int = 100) -> ModelCandidate:
    return ModelCandidate(
        candidate_id=identifier,
        provider_id=identifier,
        model_family=identifier,
        deployment_id=identifier,
        region="in",
        failure_domain=identifier,
        healthy=True,
        context_limit=32_000,
        estimated_cost_usd=cost,
        estimated_latency_ms=latency,
        capabilities=frozenset({"reasoning"}),
    )


def test_layer_planner_pre_admits_worst_case_and_separates_aggregator() -> None:
    plan = LayerPlanner().plan(
        candidates=(_candidate("a"), _candidate("b"), _candidate("aggregate")),
        layers=2,
        fan_out=2,
        quorum=2,
        aggregator_id="aggregate",
        maximum_cost_usd=1,
        deadline=datetime.now(UTC) + timedelta(seconds=2),
        replacement_waves=1,
    )
    assert len(plan.layers) == 2
    assert plan.worst_case_cost_usd == pytest.approx(1.0)
    assert all("aggregate" not in layer.deployment_ids for layer in plan.layers)


@pytest.mark.parametrize(
    "updates",
    [
        {"layers": 0},
        {"quorum": 0},
        {"maximum_cost_usd": 0.1},
        {"deadline": datetime.now(UTC) + timedelta(milliseconds=1)},
        {"fan_out": 3, "aggregator_id": "a"},
    ],
)
def test_layer_planner_rejects_invalid_or_unadmitted_plans(updates: dict) -> None:
    arguments = {
        "candidates": (_candidate("a"), _candidate("b"), _candidate("aggregate")),
        "layers": 1,
        "fan_out": 2,
        "quorum": 2,
        "aggregator_id": "aggregate",
        "maximum_cost_usd": 1,
        "deadline": datetime.now(UTC) + timedelta(seconds=2),
        "replacement_waves": 0,
    }
    arguments.update(updates)
    with pytest.raises(ValueError):
        LayerPlanner().plan(**arguments)
