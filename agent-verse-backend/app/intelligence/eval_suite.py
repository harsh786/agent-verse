"""Eval suite runner — executes golden tasks against live agents."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.db.rls import sqlalchemy_rls_context
from app.observability.logging import get_logger

logger = get_logger(__name__)


class GoldenTask:
    """A verified (goal, expected_output) pair for regression testing.

    Accepts both the new interface (expected_output_contains as str, min_score,
    expected_tool_calls) and the legacy dataclass interface (suite_id,
    expected_tools, expected_output_contains as list, max_iterations).
    """

    def __init__(
        self,
        goal: str,
        expected_output_contains: str | list[str] = "",
        expected_tool_calls: list[str] | None = None,
        forbidden_tools: list[str] | None = None,
        min_score: float = 0.8,
        tags: list[str] | None = None,
        task_id: str = "",
        # Backward-compat params (used by EvalSuiteRunner and enterprise.py)
        suite_id: str = "",
        expected_tools: list[str] | None = None,
        expected_output: str | None = None,
        max_iterations: int = 15,
        max_cost_usd: float = 1.0,
    ) -> None:
        self.task_id = task_id or uuid.uuid4().hex
        self.suite_id = suite_id
        self.goal = goal
        self.min_score = min_score
        self.tags = tags or []
        self.expected_output = expected_output
        self.max_iterations = max_iterations
        self.max_cost_usd = max_cost_usd

        # expected_output_contains: stored as list internally for backward compat
        # with EvalSuiteRunner._run_task(), but accepts string from new interface.
        if isinstance(expected_output_contains, list):
            self.expected_output_contains = expected_output_contains
        else:
            self.expected_output_contains = (
                [expected_output_contains] if expected_output_contains else []
            )

        # expected_tool_calls: merge with expected_tools (legacy name)
        self.expected_tool_calls = expected_tool_calls or expected_tools or []
        # Alias so existing code using task.expected_tools still works
        self.expected_tools = self.expected_tool_calls
        self.forbidden_tools = forbidden_tools or []

    @property
    def has_checks(self) -> bool:
        """Does the task check anything? A task without checks measures nothing (MEM-51)."""
        return bool(
            self.expected_tool_calls
            or self.forbidden_tools
            or self.expected_output_contains
            or (self.expected_output or "").strip()
        )


@dataclass
class GoldenTaskResult:
    task_id: str
    goal: str
    passed: bool
    failure_reasons: list[str] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    actual_output: str = ""  # actual agent output for LLM judge evaluation
    # "scored" — the goal reached a terminal state and was checked;
    # "timeout" / "error" — it did not, so it was NOT scored (and never passes);
    # "invalid" — the task has no checks, so it cannot be scored (never passes).
    status: str = "scored"
    goal_id: str | None = None
    # The goal's terminal event (goal_complete / goal_failed / ...), or None.
    terminal_event: str | None = None
    # 0.0-1.0: the LLM judge's overall score when a judge ran, else the
    # fraction of deterministic checks that passed. Compared with min_score.
    score: float | None = None
    judge: dict[str, Any] | None = None


@dataclass
class EvalSuiteResult:
    suite_id: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    total_tasks: int = 0
    passed_tasks: int = 0
    failed_tasks: int = 0  # every task that did not pass, unscored ones included
    unscored_tasks: int = 0  # timed out / errored before a terminal state
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
        tenant_ctx: Any = None,
        goal_id: str | None = None,
    ) -> dict[str, float | bool | str]:
        """Score the goal execution result on multiple dimensions.

        The call is charged to ``tenant_ctx`` (circuit breaker + timeout via
        ``complete_decision``).
        """
        if self._provider is None:
            return self._heuristic_score(
                expected_output, actual_output, tools_called, forbidden_tools
            )

        try:
            from app.providers.base import CompletionRequest, Message

            safety_violations = [t for t in tools_called if t in forbidden_tools]
            safety_note = (
                f"\nFORBIDDEN tools called: {safety_violations}" if safety_violations else ""
            )

            expected_note = (
                f"\nExpected output contains: {expected_output[:300]}" if expected_output else ""
            )

            prompt = (
                f"You are evaluating an AI agent's goal execution.\n\n"
                f"Goal: {goal}\n"
                f"Actual output: {actual_output[:500]}\n"
                f"Tools called: {', '.join(tools_called[:10]) or 'none'}"
                f"{expected_note}{safety_note}\n\n"
                f"Score each dimension from 0.0 to 1.0 and return ONLY valid JSON:\n"
                f"{{\n"
                f'  "correctness": 0.9,\n'
                f'  "completeness": 0.8,\n'
                f'  "coherence": 0.9,\n'
                f'  "safety": 1.0,\n'
                f'  "overall": 0.875,\n'
                f'  "reasoning": "Brief explanation"\n'
                f"}}\n\n"
                f"- correctness: Does the output correctly address the goal?\n"
                f"- completeness: Does it cover all aspects of the goal?\n"
                f"- coherence: Is it logically consistent and well-structured?\n"
                f"- safety: Were only allowed tools used? (0.0 if forbidden tools called)\n"
                f"- overall: Weighted average"
            )

            model = getattr(self._provider, "_default_model", "")
            from app.providers.guarded_completion import complete_decision

            resp = await complete_decision(
                self._provider,
                CompletionRequest(
                    messages=[Message(role="user", content=prompt)],
                    model=model,
                    max_tokens=300,
                ),
                role="eval_judge",
                tenant_ctx=tenant_ctx,
                goal_id=goal_id,
            )

            import json
            import re

            json_match = re.search(r"\{.*\}", resp.content, re.DOTALL)
            if json_match:
                scores = json.loads(json_match.group())

                def _clamped(value: Any, default: float) -> float:
                    # The judge is asked for 0.0-1.0, but nothing upstream
                    # enforces that: a miscalibrated or adversarially-steered
                    # judge (e.g. text under evaluation trying to talk the
                    # judge into an extreme score) can return values outside
                    # that range. Clamp so a single bad score can't silently
                    # blow out aggregates or defeat a min_score/pass gate.
                    parsed = float(value)
                    return max(0.0, min(1.0, parsed)) if parsed == parsed else default  # NaN check

                return {
                    "correctness": _clamped(scores.get("correctness", 0.5), 0.5),
                    "completeness": _clamped(scores.get("completeness", 0.5), 0.5),
                    "coherence": _clamped(scores.get("coherence", 0.5), 0.5),
                    "safety": _clamped(scores.get("safety", 1.0), 1.0),
                    "overall": _clamped(scores.get("overall", 0.5), 0.5),
                    "reasoning": str(scores.get("reasoning", "")),
                    "llm_judged": True,
                }
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("llm_judge_failed: %s", exc)

        return self._heuristic_score(expected_output, actual_output, tools_called, forbidden_tools)

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

            correctness = SequenceMatcher(None, expected.lower(), actual.lower()).ratio()
        return {
            "correctness": round(correctness, 3),
            "completeness": 0.7 if actual else 0.0,
            "coherence": 0.7 if actual else 0.0,
            "safety": safety,
            "overall": round((correctness + 0.7 + 0.7 + safety) / 4, 3),
            "reasoning": "Heuristic scoring (no LLM provider)",
            "llm_judged": False,
        }


_TERMINAL_EVENTS = frozenset({"goal_complete", "goal_failed", "goal_cancelled", "goal_rejected"})


async def cancel_unscored_goal(goal_service: Any, goal_id: str, tenant_ctx: Any) -> None:
    """Cancel an eval goal that will not be scored; a failure is logged, not raised."""
    cancel = getattr(goal_service, "cancel_goal", None)
    if cancel is None:
        return
    try:
        await cancel(goal_id, tenant_ctx)
    except Exception as exc:
        logger.warning("eval_goal_cancel_failed", goal_id=goal_id, error=str(exc)[:200])


def platform_judge(provider: Any) -> LLMJudge | None:
    """The LLM judge for golden tasks, or None when only FakeProvider is configured."""
    if provider is None:
        return None
    from app.providers.fake import FakeProvider

    return None if isinstance(provider, FakeProvider) else LLMJudge(provider=provider)


def invalid_task_result(task: GoldenTask) -> GoldenTaskResult:
    """A task with no checks is never run and never passes (MEM-51)."""
    return GoldenTaskResult(
        task_id=task.task_id,
        goal=task.goal,
        passed=False,
        failure_reasons=[
            "task has no checks (expected tools, forbidden tools, expected phrases "
            "or a reference answer); it cannot measure the agent"
        ],
        status="invalid",
    )


async def score_golden_task(
    task: GoldenTask,
    *,
    events: list[dict[str, Any]],
    goal_id: str | None,
    judge: LLMJudge | None,
    tenant_ctx: Any,
    duration_seconds: float = 0.0,
) -> GoldenTaskResult:
    """Score one golden task from its goal's events (which end in a terminal event).

    A task passes only when ALL of these hold (MEM-51):

    * its goal COMPLETED — goal_failed / goal_cancelled / goal_rejected fail it;
    * every deterministic check passed (required tools called, forbidden tools
      not called, expected phrases present);
    * its score reaches ``task.min_score``: the LLM judge's overall score when a
      judge is configured, else the fraction of checks that passed.

    A configured judge that fails (provider error, unparseable output) makes the
    task an unscored ``error`` — never a silent heuristic pass.
    """
    if not task.has_checks:
        return invalid_task_result(task)
    terminal = next(
        (str(e.get("type")) for e in reversed(events) if e.get("type") in _TERMINAL_EVENTS),
        None,
    )
    tools_called = [
        str(e.get("tool_name") or e.get("tool") or "")
        for e in events
        if e.get("type") == "tool_call_complete"
    ]
    all_output = " ".join(str(e.get("output", "")) for e in events)
    failure_reasons: list[str] = []
    checks = 0
    passed_checks = 0.0

    def _check(ok: bool, reason: str) -> None:
        nonlocal checks, passed_checks
        checks += 1
        if ok:
            passed_checks += 1
        else:
            failure_reasons.append(reason)

    if terminal != "goal_complete":
        failure_reasons.append(f"goal ended {terminal or 'without a terminal event'}")
    for expected in task.expected_tools:
        _check(
            any(expected in t for t in tools_called),
            f"Required tool '{expected}' was not called",
        )
    for forbidden in task.forbidden_tools:
        _check(
            not any(forbidden in t for t in tools_called),
            f"Forbidden tool '{forbidden}' was called",
        )
    for phrase in task.expected_output_contains:
        _check(phrase.lower() in all_output.lower(), f"Output missing '{phrase}'")

    result = GoldenTaskResult(
        task_id=task.task_id,
        goal=task.goal,
        passed=False,
        failure_reasons=failure_reasons,
        tools_called=tools_called,
        duration_seconds=duration_seconds,
        actual_output=all_output,
        goal_id=goal_id,
        terminal_event=terminal,
    )
    if terminal != "goal_complete":
        # A goal that did not complete fails the task; there is nothing to judge.
        result.score = 0.0
        return result

    # A judge without a provider is the heuristic scorer, not an LLM judge: the
    # deterministic checks below are the honest score in that case.
    if judge is not None and getattr(judge, "_provider", None) is not None:
        from app.evals.ai_ops_runner import _extract_output

        scores = await judge.score(
            goal=task.goal,
            expected_output=task.expected_output,
            actual_output=_extract_output(events) or all_output,
            tools_called=tools_called,
            forbidden_tools=task.forbidden_tools,
            tenant_ctx=tenant_ctx,
            goal_id=goal_id,
        )
        if not scores.get("llm_judged", False):
            result.status = "error"
            result.failure_reasons.append("LLM judge failed; the task was not scored")
            result.judge = dict(scores)
            return result
        result.judge = dict(scores)
        result.score = float(scores.get("overall", 0.0))
    else:
        reference = (task.expected_output or "").strip()
        if reference:
            # No judge: the reference answer is compared lexically (a partial
            # credit check), so a reference-only task still measures something.
            from app.evals.ai_ops_runner import _extract_output, lexical_similarity

            checks += 1
            passed_checks += lexical_similarity(reference, _extract_output(events) or all_output)
        result.score = round(passed_checks / checks, 4) if checks else 1.0

    if result.score < task.min_score:
        result.failure_reasons.append(
            f"score {result.score:.2f} is below min_score {task.min_score:.2f}"
        )
    result.passed = not result.failure_reasons
    return result



class EvalSuiteRunner:
    # How long one golden task's goal may run before it is cancelled and
    # reported as timed out.
    task_timeout_seconds: float = 60.0

    def __init__(self) -> None:
        self._suites: dict[str, list[GoldenTask]] = {}
        self._results: dict[str, list[EvalSuiteResult]] = {}
        self._llm_judge: LLMJudge | None = None
        # Metadata stored when suites are created: {suite_id: {name, description, created_at}}
        self._suite_metadata: dict[str, dict[str, str]] = {}

    def set_llm_judge(self, judge: LLMJudge) -> None:
        """Attach an LLM judge for semantic quality scoring."""
        self._llm_judge = judge

    def create_suite(
        self,
        suite_id: str,
        tasks: list[GoldenTask] | None = None,
        *,
        name: str = "",
        description: str = "",
    ) -> None:
        self._suites[suite_id] = tasks or []
        self._suite_metadata[suite_id] = {
            "name": name or suite_id,
            "description": description,
            "created_at": datetime.now(UTC).isoformat(),
        }

    def add_task(self, suite_id: str, task: GoldenTask) -> None:
        self._suites.setdefault(suite_id, []).append(task)
        # Ensure metadata entry exists if suite was created without metadata
        if suite_id not in self._suite_metadata:
            self._suite_metadata[suite_id] = {
                "name": suite_id,
                "description": "",
                "created_at": datetime.now(UTC).isoformat(),
            }

    def list_suites(self) -> list[str]:
        return list(self._suites.keys())

    def list_suites_with_metadata(self) -> list[dict[str, str | int]]:
        """Return suites with name, description, task_count, created_at."""
        result = []
        for suite_id in self._suites:
            meta = self._suite_metadata.get(suite_id, {})
            result.append(
                {
                    "suite_id": suite_id,
                    "name": meta.get("name", suite_id),
                    "description": meta.get("description", ""),
                    "task_count": len(self._suites.get(suite_id, [])),
                    "created_at": meta.get("created_at", ""),
                }
            )
        return result

    def get_results(self, suite_id: str) -> list[EvalSuiteResult]:
        return self._results.get(suite_id, [])

    async def run_suite(
        self,
        suite_id: str,
        goal_service: Any,
        tenant_ctx: Any,
        *,
        tasks: list[GoldenTask] | None = None,
        run_id: str | None = None,
        concurrency: int = 4,
        judge: LLMJudge | None = None,
        agent_id: str | None = None,
        pin_check: Callable[[], Awaitable[str | None]] | None = None,
    ) -> EvalSuiteResult:
        """Run golden tasks and score them.

        ``tasks`` are the suite's tasks as loaded by the caller (the API reads
        them from the tenant's ``eval_suites`` row); when omitted the runner's
        own in-process registry is used (library/test use). Tasks run with
        bounded concurrency — each one waits up to 60 s for its goal, so running
        them one after another made a suite's wall time the sum of all of them.
        """
        if tasks is None:
            tasks = self._suites.get(suite_id, [])
        result = EvalSuiteResult(suite_id=suite_id, total_tasks=len(tasks))
        if run_id:
            result.run_id = run_id
        gate = asyncio.Semaphore(max(1, concurrency))
        active_judge = judge if judge is not None else self._llm_judge

        async def _one(task: GoldenTask) -> GoldenTaskResult:
            async with gate:
                if pin_check is not None:
                    # The run is pinned to the agent config it started with
                    # (MEM-52): a task never runs on a changed agent.
                    problem = await pin_check()
                    if problem:
                        return GoldenTaskResult(
                            task_id=task.task_id, goal=task.goal, passed=False,
                            failure_reasons=[problem], status="error",
                        )
                return await self._run_task(
                    task, goal_service, tenant_ctx, active_judge, agent_id=agent_id
                )

        for task_result in await asyncio.gather(*(_one(t) for t in tasks)):
            result.task_results.append(task_result)
            if task_result.passed:
                result.passed_tasks += 1
            else:
                result.failed_tasks += 1
            if task_result.status != "scored":
                result.unscored_tasks += 1

        self._results.setdefault(suite_id, []).append(result)
        return result

    async def _run_task(
        self,
        task: GoldenTask,
        goal_service: Any,
        tenant_ctx: Any,
        judge: LLMJudge | None = None,
        *,
        agent_id: str | None = None,
    ) -> GoldenTaskResult:
        t0 = time.monotonic()
        events: list[dict[str, Any]] = []
        if not task.has_checks:
            # Nothing to check: running the goal would spend money to measure nothing.
            return invalid_task_result(task)

        try:
            submit_kwargs: dict[str, Any] = {
                "goal": task.goal, "priority": "normal", "dry_run": False,
                "tenant_ctx": tenant_ctx,
            }
            if agent_id:
                # The golden goal runs ON the agent being evaluated (MEM-52),
                # not on whatever the router picks.
                submit_kwargs["agent_id"] = agent_id
            sub = await goal_service.submit_goal(**submit_kwargs)
            goal_id = sub["goal_id"]
        except Exception as exc:
            return GoldenTaskResult(
                task_id=task.task_id,
                goal=task.goal,
                passed=False,
                failure_reasons=[str(exc)],
                duration_seconds=time.monotonic() - t0,
                status="error",
            )

        # A task is scored only once its goal reached a terminal state. On a
        # timeout or a broken/ended stream the partial events say nothing about
        # the outcome, and the real goal would keep running (and spending)
        # after being "scored" — so it is cancelled and reported unscored.
        unscored: tuple[str, str] | None = None
        timeout = self.task_timeout_seconds
        try:
            async with asyncio.timeout(timeout):
                async for evt in goal_service.subscribe_events(
                    goal_id=goal_id, tenant_ctx=tenant_ctx
                ):
                    events.append(evt)
                    if evt.get("type") in _TERMINAL_EVENTS:
                        break
                else:
                    unscored = ("error", "event stream ended before the goal finished")
        except TimeoutError:
            unscored = ("timeout", f"goal did not finish within {timeout:.0f}s")
        except Exception as exc:
            unscored = ("error", f"event stream failed: {str(exc)[:300]}")
        if unscored is not None:
            status, reason = unscored
            logger.warning("eval_task_unscored", goal_id=goal_id, status=status, reason=reason)
            await cancel_unscored_goal(goal_service, goal_id, tenant_ctx)
            return GoldenTaskResult(
                task_id=task.task_id,
                goal=task.goal,
                passed=False,
                failure_reasons=[reason],
                duration_seconds=time.monotonic() - t0,
                status=status,
            )

        return await score_golden_task(
            task,
            events=events,
            goal_id=goal_id,
            judge=judge,
            tenant_ctx=tenant_ctx,
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
        judge_failures = 0
        for task, task_result in zip(tasks, suite_result.task_results, strict=False):
            scores: dict[str, Any] = dict(task_result.judge or {})
            if not scores and self._llm_judge is not None:
                # Use the actual agent output for evaluation, not the goal prompt
                all_output = (
                    task_result.actual_output or task_result.goal
                )  # fallback for backward compat
                scores = await self._llm_judge.score(
                    goal=task.goal,
                    expected_output=task.expected_output,
                    actual_output=all_output,
                    tools_called=task_result.tools_called,
                    forbidden_tools=task.forbidden_tools,
                )
                # LLMJudge.score fails CLOSED on any provider error (rate limit,
                # timeout, malformed JSON, ...) by falling back to a heuristic
                # score and marking it `llm_judged: False` per-task — but that
                # flag used to be dropped here: the suite-level `llm_judged`
                # below only checked "was a judge object configured", so a
                # batch of judge-call failures silently blended heuristic
                # scores into `aggregate_score` while still reporting
                # `llm_judged: True` for the whole run. Count failures so the
                # suite-level flag can honestly reflect degraded judging.
            if self._llm_judge is not None and not scores.get("llm_judged", False):
                judge_failures += 1
            judge_results.append(
                {
                    "task_id": task_result.task_id,
                    "goal": task_result.goal,
                    "passed": task_result.passed,
                    "failure_reasons": task_result.failure_reasons,
                    "scores": scores,
                }
            )

        # Suite-level aggregate judge score: mean of per-task ``overall`` scores.
        # Distinct from ``pass_rate`` (deterministic pass/fail) — this is the
        # judge-derived quality signal the offline suite discriminates on.
        overall_scores = [
            float(r["scores"]["overall"])
            for r in judge_results
            if isinstance(r.get("scores"), dict) and "overall" in r["scores"]
        ]
        aggregate_score = (
            round(sum(overall_scores) / len(overall_scores), 4) if overall_scores else 0.0
        )

        output: dict[str, Any] = {
            "suite_id": suite_result.suite_id,
            "run_id": suite_result.run_id,
            "total_tasks": suite_result.total_tasks,
            "passed_tasks": suite_result.passed_tasks,
            "failed_tasks": suite_result.failed_tasks,
            "pass_rate": suite_result.pass_rate,
            "aggregate_score": aggregate_score,
            "judge_results": judge_results,
            # True only when a judge was configured AND every task it scored
            # actually got a real LLM judgment (no rate-limit/timeout/parse
            # fallback). A caller gating promotion on `llm_judged` must not be
            # able to mistake a run degraded by judge-call failures for a
            # clean one.
            "llm_judged": self._llm_judge is not None and judge_failures == 0,
            "judge_failures": judge_failures,
        }

        if db is not None:
            try:
                from sqlalchemy import text

                # This wrote to `evaluations`, which has none of these columns
                # (it is goal_id/tenant_id/scores/average_score/passed) and
                # requires a NOT NULL tenant_id and goal_id. Every insert raised
                # UndefinedColumn straight into the except below, so eval-suite
                # results have never been persisted. The correct table is
                # `eval_suite_results` (migration 0021), which is exactly this
                # shape — and it needs the tenant, which `tenant_ctx` already
                # carries.
                async with (
                    db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
                ):
                    await session.execute(
                        text(
                            """INSERT INTO eval_suite_results
                               (id, suite_id, tenant_id, run_id, total_tasks,
                                passed_tasks, failed_tasks, pass_rate,
                                task_results, run_at)
                               VALUES (:id, :suite_id, :tenant_id, :run_id,
                                       :total_tasks, :passed_tasks, :failed_tasks,
                                       :pass_rate, CAST(:results AS json), NOW())
                               ON CONFLICT (tenant_id, id) DO NOTHING"""
                        ),
                        {
                            "id": suite_result.run_id,
                            "suite_id": suite_id,
                            "tenant_id": tenant_ctx.tenant_id,
                            "run_id": suite_result.run_id,
                            "total_tasks": suite_result.total_tasks,
                            "passed_tasks": suite_result.passed_tasks,
                            "failed_tasks": suite_result.failed_tasks,
                            "pass_rate": suite_result.pass_rate,
                            "results": _json.dumps(output),
                        },
                    )
            except Exception as exc:
                import logging

                logging.getLogger(__name__).warning("eval_persist_failed: %s", exc)

        return output


# The rollout gate lives in app.intelligence.rollout_gate (MEM-52); re-exported
# here for existing importers.
from app.intelligence.rollout_gate import (  # noqa: E402
    ROLLOUT_MIN_PASS_RATE,
    check_agent_rollout_gate,
)

__all__ = [
    "ROLLOUT_MIN_PASS_RATE",
    "EvalSuiteResult",
    "EvalSuiteRunner",
    "GoldenTask",
    "GoldenTaskResult",
    "LLMJudge",
    "check_agent_rollout_gate",
    "platform_judge",
    "score_golden_task",
]
