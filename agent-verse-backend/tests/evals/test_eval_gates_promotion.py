"""Eval → gate path (Coverage-Matrix row 10).

Proves the offline eval suite doesn't just *score* — it *gates*: a below-threshold
eval result blocks promotion (fail-closed), while a known-good result promotes.

The candidate quality fed to the RegressionGate is the *actual* suite-level
aggregate produced by EvalSuiteRunner over good vs bad targets, so this wires the
two real subsystems together end to end. Fully deterministic (heuristic judge).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.state import GoalStatus
from app.evals.dataset_builder import EvalDatasetBuilder
from app.evals.regression_baseline import AggregateMetrics, BaselineKey, RegressionBaseline
from app.evals.regression_gate import RegressionGate
from app.intelligence.eval_suite import EvalSuiteRunner, GoldenTask, LLMJudge
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="eval-gate", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


class _ScriptedGoalService:
    def __init__(self, events: list[dict]) -> None:
        self._events = events

    async def submit_goal(self, *, goal, priority, dry_run, tenant_ctx):
        return {"goal_id": "scripted"}

    async def subscribe_events(self, *, goal_id, tenant_ctx):
        for evt in self._events:
            yield evt


def _dataset() -> list[GoldenTask]:
    return [
        GoldenTask(
            goal="Find the open issues",
            expected_tools=["search_issues"],
            forbidden_tools=["delete_everything"],
            expected_output_contains=["open issues"],
            expected_output="Found 5 open issues in the tracker",
        )
    ]


def _good_target() -> _ScriptedGoalService:
    return _ScriptedGoalService(
        [
            {"type": "tool_call_complete", "tool_name": "search_issues",
             "output": "Found 5 open issues in the tracker"},
            {"type": "goal_complete", "output": "Found 5 open issues in the tracker"},
        ]
    )


def _bad_target() -> _ScriptedGoalService:
    return _ScriptedGoalService(
        [
            {"type": "tool_call_complete", "tool_name": "delete_everything",
             "output": "unrelated noise"},
            {"type": "goal_complete", "output": "unrelated noise"},
        ]
    )


async def _run(target) -> float:
    """Run the offline suite over a target and return its aggregate quality score."""
    runner = EvalSuiteRunner()
    runner.set_llm_judge(LLMJudge(provider=None))  # heuristic → genuinely derived
    runner.create_suite("gate-suite", _dataset())
    out = await runner.run_with_llm_judge("gate-suite", target, _CTX)
    return float(out["aggregate_score"])


def _key() -> BaselineKey:
    return BaselineKey(
        tenant_id="eval-gate",
        cohort="internal",
        strategy_id="self_consistency",
        strategy_version="1.0.0",
        profile_version=1,
        evaluator_version="runtime-scorecard-v2",
        eval_suite_version="gate-suite-v1",
        limits_policy_version="limits-v1",
    )


def _metrics(quality: float) -> AggregateMetrics:
    return AggregateMetrics(
        quality=quality,
        safety=1.0,
        mean_cost_usd=0.10,
        p95_latency_ms=1000.0,
        coverage=0.95,
        sample_size=100,
        policy_passed=True,
        tenant_isolation_passed=True,
    )


# ─── The eval result actually GATES ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_below_threshold_eval_blocks_promotion():
    """A bad target's eval aggregate, measured against a good baseline, is BLOCKED."""
    good_quality = await _run(_good_target())
    bad_quality = await _run(_bad_target())

    # Sanity: the offline suite discriminates before we even reach the gate.
    assert good_quality > bad_quality

    baseline = RegressionBaseline(key=_key(), revision=1, metrics=_metrics(good_quality))
    gate = RegressionGate(minimum_samples=1)

    decision = gate.evaluate_promotion(
        baseline=baseline,
        candidate_key=_key(),
        candidate=_metrics(bad_quality),  # only quality differs from baseline
    )

    assert decision.passed is False, "below-threshold eval must block promotion"
    assert "quality_regression" in decision.reasons
    assert decision.recommendation == "hold"


@pytest.mark.asyncio
async def test_known_good_eval_promotes():
    """A known-good target that meets its own baseline is allowed to promote."""
    good_quality = await _run(_good_target())

    baseline = RegressionBaseline(key=_key(), revision=1, metrics=_metrics(good_quality))
    gate = RegressionGate(minimum_samples=1)

    decision = gate.evaluate_promotion(
        baseline=baseline,
        candidate_key=_key(),
        candidate=_metrics(good_quality),
    )

    assert decision.passed is True
    assert decision.recommendation == "promote"


@pytest.mark.asyncio
async def test_empty_eval_evidence_fails_closed():
    """No eval signal (aggregate 0.0) must never promote — fail closed."""
    baseline = RegressionBaseline(key=_key(), revision=1, metrics=_metrics(0.85))
    gate = RegressionGate(minimum_samples=1)

    decision = gate.evaluate_promotion(
        baseline=baseline,
        candidate_key=_key(),
        candidate=_metrics(0.0),  # no eval evidence of quality
    )

    assert decision.passed is False
    assert "quality_regression" in decision.reasons


# ─── Below-threshold runs are flagged as regression candidates ─────────────────


def test_dataset_builder_flags_low_score_run_as_regression_candidate():
    """A below-threshold run is captured as a reusable golden-task candidate."""
    builder = EvalDatasetBuilder()
    state = SimpleNamespace(
        goal_id="g-low",
        goal="Deploy the service",
        tenant_ctx=SimpleNamespace(tenant_id="eval-gate"),
        status=GoalStatus.FAILED,
    )

    candidate = builder.maybe_create(state=state, score=0.4)

    assert candidate is not None
    assert candidate["regression_candidate"] is True
    assert candidate["goal_id"] == "g-low"
    assert candidate["score"] == 0.4


def test_dataset_builder_ignores_passing_run():
    """A healthy run above threshold is not captured as a regression candidate."""
    builder = EvalDatasetBuilder()
    state = SimpleNamespace(
        goal_id="g-ok",
        goal="List issues",
        tenant_ctx=SimpleNamespace(tenant_id="eval-gate"),
        status=GoalStatus.COMPLETE,
    )

    assert builder.maybe_create(state=state, score=0.95) is None
