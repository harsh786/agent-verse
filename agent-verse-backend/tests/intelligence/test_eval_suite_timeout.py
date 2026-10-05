"""Timed-out / broken golden tasks are reported, not scored, and their goal is cancelled.

MEM-21: both the eval-suite runner and the AI-Ops dataset runner caught the
timeout, scored the task on its partial events, and left the submitted real
goal running (and spending) after it had been scored.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.intelligence.eval_suite import EvalSuiteRunner, GoldenTask


class _GoalService:
    """submit_goal succeeds; the event stream is scripted per test."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.cancelled: list[str] = []

    async def submit_goal(self, **_: Any) -> dict[str, Any]:
        return {"goal_id": "g-slow"}

    async def subscribe_events(self, *, goal_id: str, tenant_ctx: Any) -> AsyncIterator[Any]:
        yield {"type": "tool_call_complete", "tool": "search", "output": "partial"}
        if self.mode == "hang":
            await asyncio.sleep(60)
        elif self.mode == "error":
            raise ConnectionError("stream broke")
        # mode == "ended": stream closes without a terminal event

    async def cancel_goal(self, goal_id: str, tenant_ctx: Any) -> dict[str, Any]:
        self.cancelled.append(goal_id)
        return {"goal_id": goal_id, "status": "cancelled"}


def _task() -> GoldenTask:
    # A task that would PASS on the partial events (the tool was called).
    return GoldenTask(suite_id="s", goal="find it", expected_tools=["search"])


async def test_suite_task_timeout_is_reported_and_goal_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = EvalSuiteRunner()
    monkeypatch.setattr(runner, "task_timeout_seconds", 0.05)
    svc = _GoalService("hang")
    result = await runner.run_suite("s", svc, object(), tasks=[_task()])
    (task_result,) = result.task_results
    assert task_result.status == "timeout"
    assert task_result.passed is False
    assert result.passed_tasks == 0 and result.unscored_tasks == 1
    assert svc.cancelled == ["g-slow"]


@pytest.mark.parametrize("mode", ["error", "ended"])
async def test_suite_task_stream_failure_is_an_error_not_a_score(mode: str) -> None:
    svc = _GoalService(mode)
    result = await EvalSuiteRunner().run_suite("s", svc, object(), tasks=[_task()])
    (task_result,) = result.task_results
    assert task_result.status == "error" and task_result.passed is False
    assert svc.cancelled == ["g-slow"]


# The AI-Ops dataset runner no longer waits on a goal stream (P7-1): its case
# timeout / cancel behaviour is covered by tests/evals/test_ai_ops_two_slot_worker.py.
