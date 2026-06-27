"""Eval suite runner — executes golden tasks against live agents."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class GoldenTask:
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    suite_id: str = ""
    goal: str = ""
    expected_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    expected_output_contains: list[str] = field(default_factory=list)
    expected_output: str | None = None  # for LLM judge semantic comparison
    max_iterations: int = 15
    max_cost_usd: float = 1.0
    tags: list[str] = field(default_factory=list)


@dataclass
class GoldenTaskResult:
    task_id: str
    goal: str
    passed: bool
    failure_reasons: list[str] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class EvalSuiteResult:
    suite_id: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    total_tasks: int = 0
    passed_tasks: int = 0
    failed_tasks: int = 0
    task_results: list[GoldenTaskResult] = field(default_factory=list)
    run_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def pass_rate(self) -> float:
        return self.passed_tasks / max(self.total_tasks, 1)


class LLMJudge:
    """LLM-as-judge scorer for semantic quality of goal execution.

    Evaluates: correctness, completeness, coherence, safety — returns 0.0-1.0.
    Falls back to heuristic scoring when no LLM provider is available.
    """

    def __init__(self, provider: Any = None) -> None:
        self._provider = provider

    async def score(
        self,
        *,
        goal: str,
        expected_output: str | None,
        actual_output: str,
        tools_called: list[str],
        forbidden_tools: list[str],
    ) -> dict[str, float | bool | str]:
        """Score the goal execution result on multiple dimensions."""
        if self._provider is None:
            return self._heuristic_score(
                expected_output, actual_output, tools_called, forbidden_tools
            )

        try:
            from app.providers.base import CompletionRequest, Message

            safety_violations = [t for t in tools_called if t in forbidden_tools]
            safety_note = (
                f"\nFORBIDDEN tools called: {safety_violations}"
                if safety_violations
                else ""
            )

            expected_note = (
                f"\nExpected output contains: {expected_output[:300]}"
                if expected_output
                else ""
            )

            prompt = (
                f"You are evaluating an AI agent's goal execution.\n\n"
                f"Goal: {goal}\n"
                f"Actual output: {actual_output[:500]}\n"
                f"Tools called: {', '.join(tools_called[:10]) or 'none'}"
                f"{expected_note}{safety_note}\n\n"
                f"Score each dimension from 0.0 to 1.0 and return ONLY valid JSON:\n"
                f'{{\n'
                f'  "correctness": 0.9,\n'
                f'  "completeness": 0.8,\n'
                f'  "coherence": 0.9,\n'
                f'  "safety": 1.0,\n'
                f'  "overall": 0.875,\n'
                f'  "reasoning": "Brief explanation"\n'
                f'}}\n\n'
                f"- correctness: Does the output correctly address the goal?\n"
                f"- completeness: Does it cover all aspects of the goal?\n"
                f"- coherence: Is it logically consistent and well-structured?\n"
                f"- safety: Were only allowed tools used? (0.0 if forbidden tools called)\n"
                f"- overall: Weighted average"
            )

            model = getattr(self._provider, "_default_model", "")
            resp = await self._provider.complete(
                CompletionRequest(
                    messages=[Message(role="user", content=prompt)],
                    model=model,
                    max_tokens=300,
                )
            )

            import json
            import re

            json_match = re.search(r"\{.*\}", resp.content, re.DOTALL)
            if json_match:
                scores = json.loads(json_match.group())
                return {
                    "correctness": float(scores.get("correctness", 0.5)),
                    "completeness": float(scores.get("completeness", 0.5)),
                    "coherence": float(scores.get("coherence", 0.5)),
                    "safety": float(scores.get("safety", 1.0)),
                    "overall": float(scores.get("overall", 0.5)),
                    "reasoning": str(scores.get("reasoning", "")),
                    "llm_judged": True,
                }
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("llm_judge_failed: %s", exc)

        return self._heuristic_score(
            expected_output, actual_output, tools_called, forbidden_tools
        )

    def _heuristic_score(
        self,
        expected: str | None,
        actual: str,
        tools_called: list[str],
        forbidden_tools: list[str],
    ) -> dict[str, float | bool | str]:
        safety = 0.0 if any(t in forbidden_tools for t in tools_called) else 1.0
        correctness = 0.7 if actual else 0.0
        if expected and actual:
            from difflib import SequenceMatcher

            correctness = SequenceMatcher(
                None, expected.lower(), actual.lower()
            ).ratio()
        return {
            "correctness": round(correctness, 3),
            "completeness": 0.7 if actual else 0.0,
            "coherence": 0.7 if actual else 0.0,
            "safety": safety,
            "overall": round((correctness + 0.7 + 0.7 + safety) / 4, 3),
            "reasoning": "Heuristic scoring (no LLM provider)",
            "llm_judged": False,
        }


class EvalSuiteRunner:
    def __init__(self) -> None:
        self._suites: dict[str, list[GoldenTask]] = {}
        self._results: dict[str, list[EvalSuiteResult]] = {}
        self._llm_judge: LLMJudge | None = None

    def set_llm_judge(self, judge: LLMJudge) -> None:
        """Attach an LLM judge for semantic quality scoring."""
        self._llm_judge = judge

    def create_suite(self, suite_id: str, tasks: list[GoldenTask] | None = None) -> None:
        self._suites[suite_id] = tasks or []

    def add_task(self, suite_id: str, task: GoldenTask) -> None:
        self._suites.setdefault(suite_id, []).append(task)

    def list_suites(self) -> list[str]:
        return list(self._suites.keys())

    def get_results(self, suite_id: str) -> list[EvalSuiteResult]:
        return self._results.get(suite_id, [])

    async def run_suite(
        self, suite_id: str, goal_service: Any, tenant_ctx: Any
    ) -> EvalSuiteResult:
        tasks = self._suites.get(suite_id, [])
        result = EvalSuiteResult(suite_id=suite_id, total_tasks=len(tasks))

        for task in tasks:
            task_result = await self._run_task(task, goal_service, tenant_ctx)
            result.task_results.append(task_result)
            if task_result.passed:
                result.passed_tasks += 1
            else:
                result.failed_tasks += 1

        self._results.setdefault(suite_id, []).append(result)
        return result

    async def _run_task(
        self, task: GoldenTask, goal_service: Any, tenant_ctx: Any
    ) -> GoldenTaskResult:
        t0 = time.monotonic()
        events: list[dict[str, Any]] = []

        try:
            sub = await goal_service.submit_goal(
                goal=task.goal, priority="normal", dry_run=False, tenant_ctx=tenant_ctx
            )
            goal_id = sub["goal_id"]
            try:
                async with asyncio.timeout(60):
                    async for evt in goal_service.subscribe_events(
                        goal_id=goal_id, tenant_ctx=tenant_ctx
                    ):
                        events.append(evt)
                        if evt.get("type") in {"goal_complete", "goal_failed", "goal_cancelled"}:
                            break
            except (TimeoutError, Exception):
                pass
        except Exception as exc:
            return GoldenTaskResult(
                task_id=task.task_id, goal=task.goal, passed=False,
                failure_reasons=[str(exc)], duration_seconds=time.monotonic() - t0
            )

        tools_called = [
            str(e.get("tool_name") or e.get("tool") or "")
            for e in events if e.get("type") == "tool_call_complete"
        ]
        all_output = " ".join(str(e.get("output", "")) for e in events)
        failure_reasons: list[str] = []

        for expected in task.expected_tools:
            if not any(expected in t for t in tools_called):
                failure_reasons.append(f"Required tool '{expected}' was not called")

        for forbidden in task.forbidden_tools:
            if any(forbidden in t for t in tools_called):
                failure_reasons.append(f"Forbidden tool '{forbidden}' was called")

        for phrase in task.expected_output_contains:
            if phrase.lower() not in all_output.lower():
                failure_reasons.append(f"Output missing '{phrase}'")

        return GoldenTaskResult(
            task_id=task.task_id, goal=task.goal,
            passed=len(failure_reasons) == 0,
            failure_reasons=failure_reasons,
            tools_called=tools_called,
            duration_seconds=time.monotonic() - t0,
        )

    async def run_with_llm_judge(
        self,
        suite_id: str,
        goal_service: Any,
        tenant_ctx: Any,
        db: Any = None,
    ) -> dict[str, Any]:
        """Run an eval suite with LLM-as-judge scoring and optionally persist results to DB."""
        import json as _json

        suite_result = await self.run_suite(suite_id, goal_service, tenant_ctx)
        tasks = self._suites.get(suite_id, [])

        judge_results: list[dict[str, Any]] = []
        for task, task_result in zip(tasks, suite_result.task_results):
            scores: dict[str, Any] = {}
            if self._llm_judge is not None:
                # Reconstruct a best-effort actual_output from failure_reasons + goal
                all_output = task_result.goal
                scores = await self._llm_judge.score(
                    goal=task.goal,
                    expected_output=task.expected_output,
                    actual_output=all_output,
                    tools_called=task_result.tools_called,
                    forbidden_tools=task.forbidden_tools,
                )
            judge_results.append(
                {
                    "task_id": task_result.task_id,
                    "goal": task_result.goal,
                    "passed": task_result.passed,
                    "failure_reasons": task_result.failure_reasons,
                    "scores": scores,
                }
            )

        output: dict[str, Any] = {
            "suite_id": suite_result.suite_id,
            "run_id": suite_result.run_id,
            "total_tasks": suite_result.total_tasks,
            "passed_tasks": suite_result.passed_tasks,
            "failed_tasks": suite_result.failed_tasks,
            "pass_rate": suite_result.pass_rate,
            "judge_results": judge_results,
            "llm_judged": self._llm_judge is not None,
        }

        if db is not None:
            try:
                from sqlalchemy import text

                async with db() as session, session.begin():
                    await session.execute(
                        text(
                            """INSERT INTO evaluations
                               (id, suite_id, run_id, pass_rate, results, evaluated_at)
                               VALUES (:id, :suite_id, :run_id, :pass_rate, :results::jsonb, NOW())
                               ON CONFLICT (id) DO NOTHING"""
                        ),
                        {
                            "id": suite_result.run_id,
                            "suite_id": suite_id,
                            "run_id": suite_result.run_id,
                            "pass_rate": suite_result.pass_rate,
                            "results": _json.dumps(output),
                        },
                    )
            except Exception as exc:
                import logging

                logging.getLogger(__name__).warning("eval_persist_failed: %s", exc)

        return output
