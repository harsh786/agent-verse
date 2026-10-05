"""Execution + scoring for AI-Ops eval dataset runs.

A dataset run used to never execute an agent: each golden task's
``actual_output`` defaulted to its ``expected_output``, so lexical similarity
was 1.0 and every run "passed" without anything having run. Created LLM judges
were never consulted either — ``judge_model`` was only echoed back.

This module is the honest version:

* every case is submitted as a real goal through ``GoalService`` (tenant-scoped,
  same path as the eval-suite runner) and its output is read from the goal's
  events — ``expected_output`` is never substituted for ``actual_output``.
  Cases are submitted and POLLED by short, non-blocking steps
  (:mod:`app.evals.ai_ops_jobs`, P7-1): nothing waits inline on a worker slot
  that the case goals themselves need;
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

import json
import re
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

#: Cases executed concurrently per run (default; ``ai_ops_run_concurrency``).
CASE_CONCURRENCY = 4
#: Average case score a run needs to pass.
PASS_THRESHOLD = 0.7
#: Per-case lexical-similarity floor used to list a case under ``failed_tasks``.
LEXICAL_FAIL_FLOOR = 0.3

DEFAULT_JUDGE_DIMENSIONS = ["accuracy", "relevance", "completeness"]

def _extract_output(events: list[dict[str, Any]]) -> str:
    """The goal's final answer, read from its own event stream (never the expectation).

    The same reader GET /goals/{id} uses for its ``result_artifact`` (P7-2), so a
    case is scored on exactly the answer the goal shows; bridge-wrapped events
    (``{"type", "payload": ...}``) are unwrapped.
    """
    from app.services.result_artifacts import final_answer_text

    return final_answer_text(events)


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


def new_case(idx: int, task: dict[str, Any]) -> dict[str, Any]:
    """The record of one golden task before it runs."""
    task_input = str(task.get("input") or task.get("goal") or "").strip()
    expected = str(task.get("expected_output") or "")
    return {"index": idx, "input": task_input[:500], "expected": expected[:500]}


def task_input_of(task: dict[str, Any]) -> str:
    return str(task.get("input") or task.get("goal") or "").strip()


async def score_case(
    case: dict[str, Any],
    *,
    task: dict[str, Any],
    actual: str,
    judge: dict[str, Any] | None,
    provider: Any,
    tenant_ctx: Any,
    goal_id: str | None,
) -> dict[str, Any]:
    """Score a case whose goal COMPLETED with ``actual`` as its final answer."""
    task_input = task_input_of(task)
    expected = str(task.get("expected_output") or "")
    case["actual"] = actual[:1000]
    case["lexical_similarity"] = round(lexical_similarity(expected, actual), 3)
    if judge is not None:
        verdict = await judge_case(
            provider=provider,
            judge=judge,
            task_input=task_input,
            expected=expected,
            actual=actual,
            tenant_ctx=tenant_ctx,
            goal_id=goal_id,
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


def aggregate(cases: list[dict[str, Any]], judge: dict[str, Any] | None) -> dict[str, Any]:
    """The run outcome from its finished cases (unscored cases count as 0)."""
    n = max(len(cases), 1)
    avg_score = sum(float(c.get("score", 0.0)) for c in cases) / n
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
