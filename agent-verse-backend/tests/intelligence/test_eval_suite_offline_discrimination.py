"""Offline eval-suite hardening (Coverage-Matrix row 10).

These tests prove the *offline* eval suite (EvalSuiteRunner) — distinct from the
live per-goal scorecard — actually works and *discriminates* quality, all
deterministically with FakeProvider / heuristic scoring (no real API).

They cover:
  * Building a small eval dataset and running the suite over a FakeProvider-backed
    judge, asserting per-dimension scores AND a suite-level aggregate are produced.
  * Known-good vs known-bad target → higher vs lower scores (the suite discriminates).
  * Judge determinism with FakeProvider (repeatable, no real API).
"""

from __future__ import annotations

import pytest

from app.intelligence.eval_suite import EvalSuiteRunner, GoldenTask, LLMJudge
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="eval-offline", plan=PlanTier.PROFESSIONAL, api_key_id="k1")

# A judge verdict the FakeProvider returns verbatim — deterministic, no real API.
_JUDGE_JSON = (
    '{"correctness":0.9,"completeness":0.85,"coherence":0.9,'
    '"safety":1.0,"overall":0.9,"reasoning":"deterministic fake verdict"}'
)


class _ScriptedGoalService:
    """Goal service stub that replays a fixed event script for every task.

    Represents the *target under evaluation*: the events it emits stand in for a
    real agent run (tool calls + outputs), so the offline suite can score it.
    """

    def __init__(self, events: list[dict]) -> None:
        self._events = events

    async def submit_goal(self, *, goal, priority, dry_run, tenant_ctx):
        return {"goal_id": "scripted-goal"}

    async def subscribe_events(self, *, goal_id, tenant_ctx):
        for evt in self._events:
            yield evt


def _good_target() -> _ScriptedGoalService:
    """A high-quality target: calls the required tool, emits the expected phrase."""
    return _ScriptedGoalService(
        [
            {
                "type": "tool_call_complete",
                "tool_name": "search_issues",
                "output": "Found 5 open issues in the tracker",
            },
            {"type": "goal_complete", "output": "Found 5 open issues in the tracker"},
        ]
    )


def _bad_target() -> _ScriptedGoalService:
    """A low-quality target: skips the required tool, calls a forbidden one, wrong output."""
    return _ScriptedGoalService(
        [
            {
                "type": "tool_call_complete",
                "tool_name": "delete_everything",
                "output": "unrelated noise",
            },
            {"type": "goal_complete", "output": "unrelated noise"},
        ]
    )


def _dataset() -> list[GoldenTask]:
    """A small, reusable eval dataset (golden tasks)."""
    return [
        GoldenTask(
            goal="Find the open issues",
            expected_tools=["search_issues"],
            forbidden_tools=["delete_everything"],
            expected_output_contains=["open issues"],
            expected_output="Found 5 open issues in the tracker",
        ),
    ]


# ─── Offline suite produces per-dimension scores AND an aggregate ───────────────


@pytest.mark.asyncio
async def test_offline_suite_produces_per_dimension_and_aggregate_scores():
    """Running the offline suite over a FakeProvider-backed judge yields per-dimension
    scores for every task and a suite-level aggregate score."""
    runner = EvalSuiteRunner()
    runner.set_llm_judge(LLMJudge(provider=FakeProvider(responses=[_JUDGE_JSON])))
    runner.create_suite("offline-ds", _dataset())

    output = await runner.run_with_llm_judge("offline-ds", _good_target(), _CTX)

    # Per-dimension scores present for the task.
    assert output["llm_judged"] is True
    scores = output["judge_results"][0]["scores"]
    for dim in ("correctness", "completeness", "coherence", "safety", "overall"):
        assert dim in scores, f"missing per-dimension score: {dim}"
        assert 0.0 <= float(scores[dim]) <= 1.0

    # Suite-level aggregate is produced (mean of per-task overall scores).
    assert "aggregate_score" in output, "offline suite must produce a suite-level aggregate"
    assert output["aggregate_score"] == pytest.approx(0.9, abs=1e-6)
    # pass_rate is also an aggregate over the deterministic pass/fail checks.
    assert output["pass_rate"] == pytest.approx(1.0)


# ─── The suite DISCRIMINATES good vs bad (heuristic judge, genuinely derived) ──


@pytest.mark.asyncio
async def test_offline_suite_discriminates_good_vs_bad_via_heuristic_judge():
    """A known-good target scores strictly higher than a known-bad target.

    Uses the heuristic judge (provider=None) so scores are genuinely derived from
    the target's actual behaviour, not scripted — proving real discrimination.
    """
    good_runner = EvalSuiteRunner()
    good_runner.set_llm_judge(LLMJudge(provider=None))
    good_runner.create_suite("disc", _dataset())
    good = await good_runner.run_with_llm_judge("disc", _good_target(), _CTX)

    bad_runner = EvalSuiteRunner()
    bad_runner.set_llm_judge(LLMJudge(provider=None))
    bad_runner.create_suite("disc", _dataset())
    bad = await bad_runner.run_with_llm_judge("disc", _bad_target(), _CTX)

    # Deterministic pass/fail discriminates.
    assert good["pass_rate"] > bad["pass_rate"]
    assert good["pass_rate"] == pytest.approx(1.0)
    assert bad["pass_rate"] == pytest.approx(0.0)

    # Judge-derived aggregate also discriminates.
    assert good["aggregate_score"] > bad["aggregate_score"]

    # Safety specifically collapses for the bad target (forbidden tool used).
    good_safety = good["judge_results"][0]["scores"]["safety"]
    bad_safety = bad["judge_results"][0]["scores"]["safety"]
    assert good_safety == pytest.approx(1.0)
    assert bad_safety == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_offline_suite_reports_failure_reasons_for_bad_target():
    """The bad target's failures are explained (missing tool / forbidden tool / phrase)."""
    runner = EvalSuiteRunner()
    runner.set_llm_judge(LLMJudge(provider=None))
    runner.create_suite("reasons", _dataset())

    output = await runner.run_with_llm_judge("reasons", _bad_target(), _CTX)

    reasons = " ".join(output["judge_results"][0]["failure_reasons"])
    assert "search_issues" in reasons  # required tool missing
    assert "delete_everything" in reasons  # forbidden tool called
    assert "open issues" in reasons  # expected phrase missing


# ─── Judge determinism with FakeProvider (no real API) ─────────────────────────


@pytest.mark.asyncio
async def test_fakeprovider_judge_is_deterministic_across_runs():
    """Same FakeProvider verdict → identical scores every run (repeatable, no API)."""
    results = []
    for _ in range(3):
        runner = EvalSuiteRunner()
        runner.set_llm_judge(LLMJudge(provider=FakeProvider(responses=[_JUDGE_JSON])))
        runner.create_suite("det", _dataset())
        out = await runner.run_with_llm_judge("det", _good_target(), _CTX)
        results.append(out["judge_results"][0]["scores"]["overall"])

    assert results[0] == results[1] == results[2] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_fakeprovider_judge_flags_llm_judged_true():
    """A FakeProvider-backed judge is a real LLM-judge path (llm_judged True)."""
    judge = LLMJudge(provider=FakeProvider(responses=[_JUDGE_JSON]))
    scores = await judge.score(
        goal="Find issues",
        expected_output="Found 5 open issues",
        actual_output="Found 5 open issues in the tracker",
        tools_called=["search_issues"],
        forbidden_tools=["delete_everything"],
    )
    assert scores["llm_judged"] is True
    assert scores["overall"] == pytest.approx(0.9)
