from app.routing_runtime.optimizer import MeasuredOutcome, RoutingOptimizer


def test_optimizer_requires_samples_and_never_weakens_hard_limits() -> None:
    optimizer = RoutingOptimizer(minimum_samples=3)
    for candidate_id, latency, quality, cost in (
        ("a", 100, 9000, 0.1),
        ("b", 3000, 9500, 0.1),
        ("c", 100, 100, 0.01),
    ):
        for _ in range(3):
            optimizer.record(MeasuredOutcome(candidate_id, True, quality, cost, latency))
    result = optimizer.recommend(
        ("a", "b", "c"), quality_floor=8000, cost_ceiling_usd=1, deadline_ms=1000
    )
    assert result.candidate_id == "a" and result.p95_latency_ms == 100
    assert (
        RoutingOptimizer()
        .recommend(("unknown",), quality_floor=0, cost_ceiling_usd=1, deadline_ms=1000)
        .candidate_id
        is None
    )


def test_hedging_requires_idempotency_budget_and_deadline_pressure() -> None:
    assert RoutingOptimizer.may_hedge(
        idempotent=True,
        remaining_budget_usd=1,
        hedge_cost_usd=0.1,
        primary_p95_ms=1000,
        remaining_deadline_ms=500,
    )
    assert not RoutingOptimizer.may_hedge(
        idempotent=False,
        remaining_budget_usd=1,
        hedge_cost_usd=0.1,
        primary_p95_ms=1000,
        remaining_deadline_ms=500,
    )
