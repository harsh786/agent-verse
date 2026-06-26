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


class EvalSuiteRunner:
    def __init__(self) -> None:
        self._suites: dict[str, list[GoldenTask]] = {}
        self._results: dict[str, list[EvalSuiteResult]] = {}

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
