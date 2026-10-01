"""MEM-51: a golden task measures the agent — its goal must COMPLETE.

A task used to pass on its tool/phrase checks whatever the terminal event was
(goal_failed / goal_cancelled / goal_rejected all passed), a task with no
checks passed unconditionally, and min_score / the LLM judge were never used
outside a library-only path. So a failing agent cleared the rollout gate.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.intelligence.eval_suite import EvalSuiteRunner, GoldenTask, LLMJudge
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="golden-outcome", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _Svc:
    def __init__(self, *events: dict[str, Any]) -> None:
        self._events = list(events)

    async def submit_goal(self, **_: Any) -> dict[str, Any]:
        return {"goal_id": "g1"}

    async def subscribe_events(self, *, goal_id: str, tenant_ctx: Any) -> Any:
        for evt in self._events:
            yield evt

    async def cancel_goal(self, goal_id: str, tenant_ctx: Any) -> dict[str, Any]:
        return {}


_TOOL_OK = {"type": "tool_call_complete", "tool_name": "search", "output": "the answer is 42"}


def _task(**kw: Any) -> GoldenTask:
    base: dict[str, Any] = {"goal": "find it", "expected_tools": ["search"]}
    base.update(kw)
    return GoldenTask(**base)


@pytest.mark.parametrize("terminal", ["goal_failed", "goal_cancelled", "goal_rejected"])
async def test_task_fails_when_goal_does_not_complete(terminal: str) -> None:
    svc = _Svc(_TOOL_OK, {"type": terminal, "reason": "boom"})
    result = await EvalSuiteRunner().run_suite("s", svc, _CTX, tasks=[_task()])
    (tr,) = result.task_results
    assert tr.passed is False
    assert tr.terminal_event == terminal
    assert any(f"goal ended {terminal}" in r for r in tr.failure_reasons)
    assert result.pass_rate == 0.0


async def test_completed_goal_with_passing_checks_passes() -> None:
    svc = _Svc(_TOOL_OK, {"type": "goal_complete"})
    result = await EvalSuiteRunner().run_suite("s", svc, _CTX, tasks=[_task()])
    (tr,) = result.task_results
    assert tr.passed is True and tr.terminal_event == "goal_complete"
    assert tr.score == 1.0


async def test_task_without_checks_is_invalid_and_never_passes() -> None:
    task = GoldenTask(goal="anything")
    assert task.has_checks is False
    svc = _Svc({"type": "goal_complete"})
    result = await EvalSuiteRunner().run_suite("s", svc, _CTX, tasks=[task])
    (tr,) = result.task_results
    assert tr.passed is False and tr.status == "invalid"
    assert result.unscored_tasks == 1


class _Judge(LLMJudge):
    def __init__(self, overall: float, *, judged: bool = True) -> None:
        super().__init__(provider=object())
        self.overall = overall
        self.judged = judged
        self.calls: list[dict[str, Any]] = []

    async def score(self, **kw: Any) -> dict[str, float | bool | str]:
        self.calls.append(kw)
        return {"overall": self.overall, "llm_judged": self.judged, "reasoning": "r"}


async def test_judge_score_below_min_score_fails() -> None:
    runner = EvalSuiteRunner()
    judge = _Judge(0.5)
    runner.set_llm_judge(judge)
    svc = _Svc(_TOOL_OK, {"type": "goal_complete"})
    result = await runner.run_suite("s", svc, _CTX, tasks=[_task(min_score=0.8)])
    (tr,) = result.task_results
    assert tr.passed is False and tr.score == 0.5
    assert any("below min_score" in r for r in tr.failure_reasons)
    # the judge is charged to the tenant whose suite runs
    assert judge.calls[0]["tenant_ctx"] is _CTX
    assert judge.calls[0]["goal_id"] == "g1"


async def test_judge_score_at_or_above_min_score_passes() -> None:
    runner = EvalSuiteRunner()
    runner.set_llm_judge(_Judge(0.9))
    svc = _Svc(_TOOL_OK, {"type": "goal_complete"})
    result = await runner.run_suite("s", svc, _CTX, tasks=[_task(min_score=0.8)])
    (tr,) = result.task_results
    assert tr.passed is True and tr.score == 0.9 and tr.judge is not None


async def test_judge_failure_is_an_unscored_error_not_a_heuristic_pass() -> None:
    runner = EvalSuiteRunner()
    runner.set_llm_judge(_Judge(0.99, judged=False))
    svc = _Svc(_TOOL_OK, {"type": "goal_complete"})
    result = await runner.run_suite("s", svc, _CTX, tasks=[_task()])
    (tr,) = result.task_results
    assert tr.passed is False and tr.status == "error"
    assert result.unscored_tasks == 1


async def test_failed_goal_is_not_sent_to_the_judge() -> None:
    runner = EvalSuiteRunner()
    judge = _Judge(1.0)
    runner.set_llm_judge(judge)
    svc = _Svc(_TOOL_OK, {"type": "goal_failed"})
    result = await runner.run_suite("s", svc, _CTX, tasks=[_task()])
    assert result.task_results[0].passed is False
    assert judge.calls == []
