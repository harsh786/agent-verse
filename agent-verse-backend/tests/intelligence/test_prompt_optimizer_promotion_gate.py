"""Auto-promotion must weigh cost and latency, not quality alone.

``maybe_promote`` used to promote any challenger with a significantly higher
mean score. It now also has to pass ``RegressionGate`` against the control:
<= +10% mean cost and <= +15% p95 latency (the gate's defaults).
"""

from __future__ import annotations

import pytest

from app.intelligence.prompt_optimizer import (
    LATENCY_BUCKETS_MS,
    PromptOptimizer,
    VariantStats,
    _latency_bucket,
    _p95_from_hist,
)


def _pair(opt: PromptOptimizer) -> tuple[str, str]:
    c = opt.register_variant("planner", "control", "a", tenant_id="t", is_control=True)
    ch = opt.register_variant("planner", "challenger", "b", tenant_id="t")
    return c.variant_id, ch.variant_id


def _feed(opt: PromptOptimizer, vid: str, score: float, *, cost: float, latency: float,
          n: int = 30) -> None:
    for i in range(n):
        opt.record_result(vid, score + (i % 3) * 0.01, cost_usd=cost, latency_ms=latency)


def test_better_and_no_more_expensive_is_promoted() -> None:
    opt = PromptOptimizer(min_runs_for_promotion=30)
    c, ch = _pair(opt)
    _feed(opt, c, 0.6, cost=0.01, latency=900)
    _feed(opt, ch, 0.8, cost=0.0105, latency=950)  # +5% cost, same latency bucket
    promoted = opt.maybe_promote("planner", tenant_id="t")
    assert promoted is not None and promoted.variant_id == ch


@pytest.mark.parametrize(
    ("cost", "latency", "reason"),
    [
        (0.03, 900, "cost_regression"),       # 3x the cost
        (0.01, 20_000, "latency_regression"),  # p95 1 s -> 20 s
    ],
)
def test_better_but_regressing_is_held(cost: float, latency: float, reason: str) -> None:
    opt = PromptOptimizer(min_runs_for_promotion=30)
    c, ch = _pair(opt)
    _feed(opt, c, 0.6, cost=0.01, latency=900)
    _feed(opt, ch, 0.9, cost=cost, latency=latency)
    assert opt.maybe_promote("planner", tenant_id="t") is None
    control = opt._variants["t"][c]
    challenger = opt._variants["t"][ch]
    verdict = opt.decide_promotion(
        VariantStats.of(control), [VariantStats.of(challenger)], tenant_id="t",
        prompt_key="planner",
    )
    assert reason in verdict.held[ch]
    assert control.is_control is True


def test_not_significant_is_held() -> None:
    opt = PromptOptimizer(min_runs_for_promotion=30)
    c, ch = _pair(opt)
    _feed(opt, c, 0.60, cost=0.01, latency=900)
    _feed(opt, ch, 0.601, cost=0.01, latency=900)
    assert opt.maybe_promote("planner", tenant_id="t") is None


def test_insufficient_samples_is_held() -> None:
    opt = PromptOptimizer(min_runs_for_promotion=30)
    c, ch = _pair(opt)
    _feed(opt, c, 0.6, cost=0.01, latency=900)
    _feed(opt, ch, 0.9, cost=0.01, latency=900, n=10)
    assert opt.maybe_promote("planner", tenant_id="t") is None


def test_latency_histogram_p95_is_conservative() -> None:
    hist = [0] * len(LATENCY_BUCKETS_MS)
    for ms in [50] * 90 + [4_000] * 10:
        hist[_latency_bucket(ms)] += 1
    # 95th percentile falls in the (2.5 s, 5 s] bucket -> reported as 5 s.
    assert _p95_from_hist(hist) == 5_000
    assert _p95_from_hist([0] * len(LATENCY_BUCKETS_MS)) == 0.0
    overflow = [0] * len(LATENCY_BUCKETS_MS)
    overflow[-1] = 1
    assert _p95_from_hist(overflow) == LATENCY_BUCKETS_MS[-2]
