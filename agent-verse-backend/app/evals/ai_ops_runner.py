"""Execution + scoring for AI-Ops eval dataset runs.

A dataset run used to never execute an agent: each golden task's
``actual_output`` defaulted to its ``expected_output``, so lexical similarity
was 1.0 and every run "passed" without anything having run. Created LLM judges
were never consulted either — ``judge_model`` was only echoed back.

This module is the honest version:

* every case is submitted as a real goal through ``GoalService`` (tenant-scoped,
  same path as the eval-suite runner) and its output is read from the goal's
  event stream — ``expected_output`` is never substituted for ``actual_output``;
* a case whose goal does not complete (failed, cancelled, timed out, submit
  rejected) fails with that reason and scores 0;
* when a judge is configured, the case is scored by LLM-as-judge through the
  tenant's provider and the per-dimension judge scores are recorded per case; a
  judge call that errors or returns unparseable output is recorded as a judge
  error (the case fails) instead of silently falling back to another scorer;
* without a judge, cases are scored by lexical overlap of the REAL output
  against ``expected_output``; a case with no ``expected_output`` is
  ``unscorable`` and fails.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

#: Seconds each case's goal may run before the case fails as timed out.
CASE_TIMEOUT_SECONDS = 120.0
#: Cases executed concurrently per run.
CASE_CONCURRENCY = 4
#: Average case score a run needs to pass.
PASS_THRESHOLD = 0.7
#: Per-case lexical-similarity floor used to list a case under ``failed_tasks``.
LEXICAL_FAIL_FLOOR = 0.3

DEFAULT_JUDGE_DIMENSIONS = ["accuracy", "relevance", "completeness"]

_TERMINAL = {"goal_complete", "goal_failed", "goal_cancelled", "goal_rejected"}


def _extract_output(events: list[dict[str, Any]]) -> str:
    """The goal's final answer, read from its own event stream (never the expectation)."""
    for evt in reversed(events):
        if evt.get("type") != "goal_complete":
            continue
        for key in ("answer", "cited_answer", "summary", "message", "output"):
            val = evt.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        result = evt.get("result")
        if isinstance(result, str) and result.strip():
            return result.strip()
        if isinstance(result, dict | list) and result:
            return json.dumps(result)[:4000]
    for evt in reversed(events):
        if evt.get("type") == "synthesis_complete":
            val = evt.get("cited_answer")
            if isinstance(val, str) and val.strip():
                return val.strip()
    for evt in reversed(events):
        if evt.get("type") == "step_complete" and evt.get("output") is not None:
            out = str(evt.get("output") or "").strip()
            if out:
                return out
    return ""


async def execute_case(
    *,
    goal_service: Any,
    tenant_ctx: Any,
    goal: str,
    agent_id: str | None,
    timeout: float = CASE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run one golden task as a real goal. Returns status/output/tools/error."""
    t0 = time.monotonic()
    events: list[dict[str, Any]] = []
    try:
        sub = await goal_service.submit_goal(
            goal=goal,
            priority="normal",
            dry_run=False,
            tenant_ctx=tenant_ctx,
            agent_id=agent_id,
        )
        goal_id = str(sub["goal_id"])
    except Exception as exc:
        return {
            "goal_id": None,
            "goal_status": "submit_failed",
            "actual_output": "",
            "tools_called": [],
            "error": str(exc)[:500],
            "duration_seconds": round(time.monotonic() - t0, 3),
        }

    status = "timeout"
    error = ""
    try:
        async with asyncio.timeout(timeout):
            async for evt in goal_service.subscribe_events(goal_id=goal_id, tenant_ctx=tenant_ctx):
                events.append(evt)
                etype = evt.get("type")
                if etype in _TERMINAL:
                    status = {
                        "goal_complete": "complete",
                        "goal_failed": "failed",
                        "goal_cancelled": "cancelled",
                        "goal_rejected": "rejected",
                    }[str(etype)]
                    if status != "complete":
                        error = str(evt.get("reason") or evt.get("error") or etype)[:500]
                    break
            else:
                status = "stream_ended"
                error = "event stream ended before the goal reached a terminal state"
    except TimeoutError:
        error = f"goal did not finish within {timeout:.0f}s"
    except Exception as exc:
        status = "stream_error"
        error = str(exc)[:500]
    if status in {"timeout", "stream_error", "stream_ended"}:
        # The case is not scored; the real goal must not keep running (and
        # spending) after the run moved on.
        from app.intelligence.eval_suite import cancel_unscored_goal

        await cancel_unscored_goal(goal_service, goal_id, tenant_ctx)

    tools_called = [
        str(e.get("tool_name") or e.get("tool") or "")
        for e in events
        if e.get("type") == "tool_call_complete"
    ]
    return {
        "goal_id": goal_id,
        "goal_status": status,
        "actual_output": _extract_output(events) if status == "complete" else "",
        "tools_called": tools_called,
        "error": error,
        "duration_seconds": round(time.monotonic() - t0, 3),
    }


def lexical_similarity(expected: str, actual: str) -> float:
    exp = set(expected.lower().split())
    if not exp:
        return 0.0
    return len(exp & set(actual.lower().split())) / len(exp)


def _render_judge_prompt(
    judge: dict[str, Any], *, task_input: str, expected: str, actual: str
) -> str:
    dims: list[str] = list(judge.get("evaluation_dimensions") or DEFAULT_JUDGE_DIMENSIONS)
    template = str(judge.get("prompt_template") or "")
    schema = "{" + ", ".join(f'"{d}": 0.0' for d in dims) + ', "reasoning": "..."}'
    if template:
        # str.replace, not str.format: the template and the agent output are
        # user-controlled and may contain braces.
        body = (
            template.replace("{input}", task_input[:2000])
            .replace("{expected}", expected[:2000])
            .replace("{expected_output}", expected[:2000])
            .replace("{actual}", actual[:4000])
            .replace("{actual_output}", actual[:4000])
        )
    else:
        body = (
            "You are grading an AI agent's answer against a reference answer.\n"
            f"Task: {task_input[:2000]}\n"
            f"Reference answer: {expected[:2000] or '(none provided)'}\n"
            f"Agent answer: {actual[:4000] or '(empty)'}"
        )
    return (
        f"{body}\n\nScore each dimension from 0.0 to 1.0: {', '.join(dims)}.\n"
        f"Return ONLY a JSON object: {schema}"
    )


async def judge_case(
    *,
    provider: Any,
    judge: dict[str, Any],
    task_input: str,
    expected: str,
    actual: str,
    tenant_ctx: Any = None,
    goal_id: str | None = None,
) -> dict[str, Any]:
    """LLM-as-judge one case. Returns {scores, score, reasoning} or {error}.

    The call is charged to the tenant running the dataset, bounded by the
    decision timeout and the per-model circuit breaker; a budget refusal is a
    judge error (nothing was spent).
    """
    from app.providers.base import CompletionRequest, Message
    from app.providers.guarded_completion import complete_decision

    dims: list[str] = list(judge.get("evaluation_dimensions") or DEFAULT_JUDGE_DIMENSIONS)
    prompt = _render_judge_prompt(judge, task_input=task_input, expected=expected, actual=actual)
    try:
        resp = await complete_decision(
            provider,
            CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                model=str(judge.get("model") or getattr(provider, "_default_model", "") or ""),
                max_tokens=400,
                json_object=True,
            ),
            role="ai_ops_judge",
            tenant_ctx=tenant_ctx,
            goal_id=goal_id,
        )
        match = re.search(r"\{.*\}", str(resp.content), re.DOTALL)
        if match is None:
            raise ValueError("judge returned no JSON object")
        raw = json.loads(match.group())
        if not isinstance(raw, dict):
            raise ValueError("judge JSON is not an object")
        scores: dict[str, float] = {}
        for dim in dims:
            if dim not in raw:
                raise ValueError(f"judge omitted dimension {dim!r}")
            val = float(raw[dim])
            if val != val:  # NaN
                raise ValueError(f"judge returned NaN for {dim!r}")
            # Clamp: a miscalibrated/steered judge must not blow out aggregates.
            scores[dim] = max(0.0, min(1.0, val))
    except Exception as exc:
        logger.warning("ai_ops_judge_failed", error=str(exc)[:300])
        return {"error": str(exc)[:300]}
    return {
        "scores": scores,
        "score": sum(scores.values()) / len(scores),
        "reasoning": str(raw.get("reasoning", ""))[:1000],
    }


async def run_dataset(
    *,
    dataset: dict[str, Any],
    goal_service: Any,
    tenant_ctx: Any,
    agent_id: str | None,
    judge: dict[str, Any] | None,
    provider: Any,
    timeout: float = CASE_TIMEOUT_SECONDS,
    concurrency: int = CASE_CONCURRENCY,
    done_cases: Mapping[int, dict[str, Any]] | None = None,
    on_case: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Execute and score every golden task. Returns the result fields to merge.

    ``done_cases`` (by case index) are cases a previous attempt of this run
    already finished and persisted: they are reused, not executed again, so a
    run resumed after a worker restart only runs what is left. ``on_case`` is
    awaited with each newly finished case (the caller persists progress).
    """
    tasks: list[dict[str, Any]] = list(dataset.get("golden_tasks") or [])
    gate = asyncio.Semaphore(max(1, concurrency))
    reuse: Mapping[int, dict[str, Any]] = done_cases or {}

    async def _one(idx: int, task: dict[str, Any]) -> dict[str, Any]:
        if idx in reuse:
            return reuse[idx]
        case = await _score_one(idx, task)
        if on_case is not None:
            await on_case(case)
        return case

    async def _score_one(idx: int, task: dict[str, Any]) -> dict[str, Any]:
        task_input = str(task.get("input") or task.get("goal") or "").strip()
        expected = str(task.get("expected_output") or "")
        case: dict[str, Any] = {"index": idx, "input": task_input[:500], "expected": expected[:500]}
        if not task_input:
            case.update(status="invalid", score=0.0, passed=False, error="golden task has no input")
            return case
        async with gate:
            exec_res = await execute_case(
                goal_service=goal_service,
                tenant_ctx=tenant_ctx,
                goal=task_input,
                agent_id=str(task.get("agent_id") or agent_id or "") or None,
                timeout=timeout,
            )
        actual = str(exec_res["actual_output"])
        case.update(
            goal_id=exec_res["goal_id"],
            goal_status=exec_res["goal_status"],
            actual=actual[:1000],
            tools_called=exec_res["tools_called"],
            duration_seconds=exec_res["duration_seconds"],
        )
        if exec_res["goal_status"] != "complete":
            case.update(
                status="execution_failed", score=0.0, passed=False, error=exec_res["error"]
            )
            return case

        case["lexical_similarity"] = round(lexical_similarity(expected, actual), 3)
        if judge is not None:
            verdict = await judge_case(
                provider=provider,
                judge=judge,
                task_input=task_input,
                expected=expected,
                actual=actual,
                tenant_ctx=tenant_ctx,
                goal_id=exec_res["goal_id"],
            )
            if "error" in verdict:
                case.update(status="judge_error", score=0.0, passed=False, error=verdict["error"])
                return case
            score = float(verdict["score"])
            case.update(
                status="scored",
                judge_scores={k: round(v, 3) for k, v in verdict["scores"].items()},
                judge_reasoning=verdict["reasoning"],
                score=round(score, 3),
                passed=score >= PASS_THRESHOLD,
            )
            return case

        if not expected.strip():
            case.update(
                status="unscorable",
                score=0.0,
                passed=False,
                error="no expected_output and no judge configured",
            )
            return case
        sim = float(case["lexical_similarity"])
        case.update(status="scored", score=round(sim, 3), passed=sim >= LEXICAL_FAIL_FLOOR)
        return case

    pending = [asyncio.ensure_future(_one(i, t)) for i, t in enumerate(tasks)]
    try:
        cases = list(await asyncio.gather(*pending))
    except BaseException:
        # Stop the sibling cases: a failed/aborted run must not keep executing
        # goals and writing progress behind the caller's back.
        for fut in pending:
            fut.cancel()
        raise

    n = max(len(cases), 1)
    avg_score = sum(float(c["score"]) for c in cases) / n
    scores: dict[str, float] = {}
    scored = [c for c in cases if c.get("status") == "scored"]
    if judge is not None:
        dims = list(judge.get("evaluation_dimensions") or DEFAULT_JUDGE_DIMENSIONS)
        for dim in dims:
            # Unscored cases count as 0 so a run cannot pass by erroring.
            scores[dim] = round(
                sum(float(c.get("judge_scores", {}).get(dim, 0.0)) for c in cases) / n, 3
            )
    scores["lexical_similarity"] = round(
        sum(float(c.get("lexical_similarity", 0.0)) for c in cases) / n, 3
    )
    failed = [c for c in cases if not c.get("passed")]
    return {
        "status": "completed",
        "scores": scores,
        "avg_score": round(avg_score, 3),
        "passed": bool(cases) and avg_score >= PASS_THRESHOLD,
        "total_cases": len(cases),
        "executed_cases": sum(1 for c in cases if c.get("goal_status") == "complete"),
        "scored_cases": len(scored),
        "judge_errors": sum(1 for c in cases if c.get("status") == "judge_error"),
        "failed_task_count": len(failed),
        "failed_tasks": [
            {
                "task": c.get("input", "")[:100],
                "expected": c.get("expected", "")[:100],
                "actual": str(c.get("actual", ""))[:100],
                "score": c.get("score", 0.0),
                "status": c.get("status"),
                "error": c.get("error", ""),
            }
            for c in failed[:5]
        ],
        "cases": cases,
        "scoring": "llm_judge" if judge is not None else "lexical",
    }
