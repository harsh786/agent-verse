from __future__ import annotations

import dataclasses

import pytest

from app.evals.regression_baseline import (
    AggregateMetrics,
    BaselineKey,
    BaselineRepository,
    RegressionBaseline,
)
from app.evals.regression_gate import RegressionGate


def _key(**changes: object) -> BaselineKey:
    values = {
        "tenant_id": "tenant-1",
        "cohort": "internal",
        "strategy_id": "self_consistency",
        "strategy_version": "1.0.0",
        "profile_version": 2,
        "evaluator_version": "runtime-scorecard-v2",
        "eval_suite_version": "reasoning-v3",
        "limits_policy_version": "limits-v2",
    }
    values.update(changes)
    return BaselineKey(**values)


def _metrics(**changes: object) -> AggregateMetrics:
    values = {
        "quality": 0.90,
        "safety": 1.0,
        "mean_cost_usd": 0.10,
        "p95_latency_ms": 1000.0,
        "coverage": 0.95,
        "sample_size": 100,
        "policy_passed": True,
        "tenant_isolation_passed": True,
    }
    values.update(changes)
    return AggregateMetrics(**values)


def test_baseline_key_contains_all_version_dimensions() -> None:
    key = _key()
    assert key.identity == (
        "tenant-1",
        "internal",
        "self_consistency",
        "1.0.0",
        2,
        "runtime-scorecard-v2",
        "reasoning-v3",
        "limits-v2",
    )


def test_baseline_revisions_are_immutable_and_append_only() -> None:
    baseline = RegressionBaseline(key=_key(), revision=1, metrics=_metrics())
    with pytest.raises(dataclasses.FrozenInstanceError):
        baseline.revision = 2  # type: ignore[misc]
    repository = BaselineRepository()
    repository.append(baseline)
    repository.append(RegressionBaseline(key=_key(), revision=2, metrics=_metrics()))
    assert [item.revision for item in repository.revisions(_key())] == [1, 2]
    with pytest.raises(ValueError, match="already exists"):
        repository.append(baseline)


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (_metrics(quality=0.879), "quality_regression"),
        (_metrics(safety=0.999), "safety_regression"),
        (_metrics(mean_cost_usd=0.111), "cost_regression"),
        (_metrics(p95_latency_ms=1151.0), "latency_regression"),
        (_metrics(coverage=0.899), "insufficient_coverage"),
        (_metrics(sample_size=29), "insufficient_samples"),
        (_metrics(policy_passed=False), "policy_gate_failed"),
        (_metrics(tenant_isolation_passed=False), "tenant_isolation_gate_failed"),
    ],
)
def test_promotion_rejects_regression_or_missing_evidence(
    candidate: AggregateMetrics, reason: str
) -> None:
    baseline = RegressionBaseline(key=_key(), revision=1, metrics=_metrics())
    decision = RegressionGate(minimum_samples=30).evaluate_promotion(
        baseline=baseline,
        candidate_key=_key(),
        candidate=candidate,
    )
    assert not decision.passed
    assert reason in decision.reasons


def test_promotion_rejects_evidence_version_mismatch() -> None:
    baseline = RegressionBaseline(key=_key(), revision=1, metrics=_metrics())
    decision = RegressionGate().evaluate_promotion(
        baseline=baseline,
        candidate_key=_key(evaluator_version="runtime-scorecard-v3"),
        candidate=_metrics(),
    )
    assert decision.reasons == ("evidence_version_mismatch",)


def test_canary_expands_only_when_every_window_passes() -> None:
    baseline = RegressionBaseline(key=_key(), revision=1, metrics=_metrics())
    gate = RegressionGate(minimum_samples=30)
    passed = gate.evaluate_canary_windows(
        baseline=baseline,
        candidate_key=_key(),
        windows=[_metrics(), _metrics(quality=0.89)],
    )
    assert passed.passed
    assert passed.recommendation == "expand"
    failed = gate.evaluate_canary_windows(
        baseline=baseline,
        candidate_key=_key(),
        windows=[_metrics(), _metrics(safety=0.9), _metrics()],
    )
    assert not failed.passed
    assert failed.recommendation == "freeze_and_recommend_kill_switch"
