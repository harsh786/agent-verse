"""Phase 17: LLM-as-judge eval tests."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# LLMJudge — provider-backed scoring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_judge_scores_correct_output():
    from app.intelligence.eval_suite import LLMJudge

    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(
        return_value=MagicMock(
            content=(
                '{"correctness":0.9,"completeness":0.8,"coherence":0.9,'
                '"safety":1.0,"overall":0.875,"reasoning":"Good"}'
            )
        )
    )
    mock_provider._default_model = ""

    judge = LLMJudge(provider=mock_provider)
    scores = await judge.score(
        goal="Find open issues",
        expected_output="issues list",
        actual_output="Found 5 open issues",
        tools_called=["github_list_issues"],
        forbidden_tools=[],
    )
    assert scores["correctness"] == 0.9
    assert scores["completeness"] == 0.8
    assert scores["coherence"] == 0.9
    assert scores["safety"] == 1.0
    assert scores["overall"] == 0.875
    assert scores["llm_judged"] is True


@pytest.mark.asyncio
async def test_llm_judge_returns_all_required_keys():
    from app.intelligence.eval_suite import LLMJudge

    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(
        return_value=MagicMock(
            content='{"correctness":0.5,"completeness":0.5,"coherence":0.5,"safety":1.0,"overall":0.5,"reasoning":"ok"}'
        )
    )
    mock_provider._default_model = "gpt-4o"

    judge = LLMJudge(provider=mock_provider)
    scores = await judge.score(
        goal="test goal",
        expected_output=None,
        actual_output="some output",
        tools_called=[],
        forbidden_tools=[],
    )
    for key in ("correctness", "completeness", "coherence", "safety", "overall", "llm_judged"):
        assert key in scores, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# LLMJudge — heuristic fallback (provider=None)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_judge_penalizes_forbidden_tools():
    from app.intelligence.eval_suite import LLMJudge

    judge = LLMJudge(provider=None)  # heuristic path
    scores = await judge.score(
        goal="Read issues",
        expected_output=None,
        actual_output="deleted issue",
        tools_called=["github_delete_issue"],
        forbidden_tools=["github_delete_issue"],
    )
    assert scores["safety"] == 0.0, "Forbidden tool must result in safety=0.0"
    assert scores["llm_judged"] is False


@pytest.mark.asyncio
async def test_llm_judge_heuristic_safe_when_no_forbidden_tools_used():
    from app.intelligence.eval_suite import LLMJudge

    judge = LLMJudge(provider=None)
    scores = await judge.score(
        goal="List issues",
        expected_output=None,
        actual_output="5 open issues found",
        tools_called=["github_list_issues"],
        forbidden_tools=["github_delete_issue"],
    )
    assert scores["safety"] == 1.0


@pytest.mark.asyncio
async def test_llm_judge_heuristic_correctness_uses_sequence_matching():
    from app.intelligence.eval_suite import LLMJudge

    judge = LLMJudge(provider=None)
    scores_match = await judge.score(
        goal="Find issues",
        expected_output="5 open issues",
        actual_output="5 open issues found",
        tools_called=[],
        forbidden_tools=[],
    )
    scores_no_match = await judge.score(
        goal="Find issues",
        expected_output="5 open issues",
        actual_output="completely unrelated content xyz",
        tools_called=[],
        forbidden_tools=[],
    )
    # Closer match should yield higher correctness
    assert scores_match["correctness"] > scores_no_match["correctness"]


@pytest.mark.asyncio
async def test_llm_judge_heuristic_empty_output_zeroes_correctness():
    from app.intelligence.eval_suite import LLMJudge

    judge = LLMJudge(provider=None)
    scores = await judge.score(
        goal="Do something",
        expected_output=None,
        actual_output="",
        tools_called=[],
        forbidden_tools=[],
    )
    assert scores["correctness"] == 0.0
    assert scores["completeness"] == 0.0
    assert scores["coherence"] == 0.0


@pytest.mark.asyncio
async def test_llm_judge_falls_back_to_heuristic_on_provider_error():
    from app.intelligence.eval_suite import LLMJudge

    failing_provider = MagicMock()
    failing_provider.complete = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
    failing_provider._default_model = ""

    judge = LLMJudge(provider=failing_provider)
    scores = await judge.score(
        goal="Find issues",
        expected_output=None,
        actual_output="some output",
        tools_called=["tool_a"],
        forbidden_tools=[],
    )
    # Should fall back to heuristic — llm_judged = False
    assert scores["llm_judged"] is False
    assert "correctness" in scores


# ---------------------------------------------------------------------------
# EvalSuiteRunner integration
# ---------------------------------------------------------------------------


def test_eval_suite_has_llm_judge():
    from app.intelligence.eval_suite import EvalSuiteRunner, LLMJudge

    assert LLMJudge is not None
    runner = EvalSuiteRunner()
    assert hasattr(runner, "set_llm_judge") or hasattr(runner, "_llm_judge")


def test_eval_suite_runner_set_llm_judge():
    from app.intelligence.eval_suite import EvalSuiteRunner, LLMJudge

    runner = EvalSuiteRunner()
    judge = LLMJudge(provider=None)
    runner.set_llm_judge(judge)
    assert runner._llm_judge is judge


def test_eval_suite_runner_initial_judge_is_none():
    from app.intelligence.eval_suite import EvalSuiteRunner

    runner = EvalSuiteRunner()
    assert runner._llm_judge is None


@pytest.mark.asyncio
async def test_run_with_llm_judge_returns_judge_results():
    """run_with_llm_judge should include LLM judge scores in the output."""
    from app.intelligence.eval_suite import EvalSuiteRunner, GoldenTask, LLMJudge

    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(
        return_value=MagicMock(
            content='{"correctness":0.8,"completeness":0.7,"coherence":0.9,"safety":1.0,"overall":0.85,"reasoning":"Fine"}'
        )
    )
    mock_provider._default_model = ""

    class _MockGoalService:
        async def submit_goal(self, *, goal, priority, dry_run, tenant_ctx):
            return {"goal_id": "mock-g"}

        async def subscribe_events(self, *, goal_id, tenant_ctx):
            yield {"type": "goal_complete"}

    runner = EvalSuiteRunner()
    runner.set_llm_judge(LLMJudge(provider=mock_provider))
    runner.create_suite("judge-suite", [GoldenTask(goal="do something")])

    from app.tenancy.context import PlanTier, TenantContext

    ctx = TenantContext(tenant_id="t-judge", plan=PlanTier.PROFESSIONAL, api_key_id="k-j")
    output = await runner.run_with_llm_judge("judge-suite", _MockGoalService(), ctx)

    assert output["suite_id"] == "judge-suite"
    assert output["llm_judged"] is True
    assert "judge_results" in output
    assert len(output["judge_results"]) == 1
    assert "scores" in output["judge_results"][0]
