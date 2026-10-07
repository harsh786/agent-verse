"""Promote a completed goal to a golden task of an eval suite (a10-F235-01).

The golden task is built from the goal's own record and event stream — the
same readers the eval runner scores with — so a later suite run checks the
agent against exactly what this run produced:

* input: the goal text, plus optional caller context;
* expected: the verified final answer (``final_answer_text``, what
  ``GET /goals/{id}`` shows), the tools the run called successfully, and the
  sources it cited (``cited_sources``);
* provenance: ``source_goal_id`` and the tags ``promoted-from-goal`` /
  ``agent:<id>``.

Only a goal that COMPLETED (its verifier accepted the answer), is not a dry
run and has an answer can be promoted. The task id is derived from the goal id,
so promoting the same goal into the same suite twice is a conflict.
"""

from __future__ import annotations

import hashlib
from typing import Any

__all__ = [
    "MAX_EXPECTED_ITEMS",
    "GoalNotPromotableError",
    "golden_task_from_goal",
    "promoted_task_id",
]

MAX_EXPECTED_ITEMS = 20
MAX_TEXT = 10_000
_COMPLETED = frozenset({"complete", "completed"})
PROMOTED_TAG = "promoted-from-goal"


class GoalNotPromotableError(ValueError):
    """The goal cannot become a golden task (not completed, dry run, no answer)."""


def promoted_task_id(goal_id: str) -> str:
    """Stable task id for a promoted goal (fits the 64-char task id)."""
    if len(goal_id) <= 59:
        return f"goal-{goal_id}"
    return "goal-" + hashlib.sha256(goal_id.encode()).hexdigest()[:40]


def _tools_called(events: list[dict[str, Any]]) -> list[str]:
    from app.services.result_artifacts import unwrap_event

    seen: dict[str, None] = {}
    for raw in events:
        event = unwrap_event(raw)
        if event.get("type") != "tool_call_complete" or event.get("success") is False:
            continue
        name = str(event.get("tool_name") or event.get("tool") or "").strip()
        if name:
            seen.setdefault(name, None)
    return list(seen)


def _dedupe(values: list[str]) -> list[str]:
    out: dict[str, None] = {}
    for v in values:
        s = str(v).strip()
        if s:
            out.setdefault(s, None)
    return list(out)


def golden_task_from_goal(
    goal: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    context: str = "",
    tags: list[str] | None = None,
    include_tools: bool = True,
    include_citations: bool = True,
    expected_output_contains: list[str] | None = None,
    min_score: float = 0.8,
    max_iterations: int = 15,
) -> dict[str, Any]:
    """The golden-task dict (store shape) for a completed goal.

    Raises :class:`GoalNotPromotableError` when the goal is not a verified,
    executed run with an answer.
    """
    from app.intelligence.eval_suite import cited_sources
    from app.services.result_artifacts import final_answer_text

    goal_id = str(goal.get("goal_id") or "")
    status = str(goal.get("status") or "").lower()
    if status not in _COMPLETED:
        raise GoalNotPromotableError(
            f"only a completed goal can be promoted (this one is {status or 'unknown'})"
        )
    if goal.get("dry_run"):
        raise GoalNotPromotableError("a dry run executed no tools; it cannot be a golden task")
    answer = final_answer_text(events).strip()
    if not answer:
        raise GoalNotPromotableError("the goal has no final answer to use as the reference")

    goal_text = str(goal.get("goal") or "").strip()
    context = context.strip()
    task_input = f"{goal_text}\n\nContext:\n{context}" if context else goal_text
    if len(task_input) > MAX_TEXT:
        raise GoalNotPromotableError(f"goal text plus context exceeds {MAX_TEXT} characters")

    auto_tags = [PROMOTED_TAG]
    if goal.get("agent_id"):
        auto_tags.append(f"agent:{goal['agent_id']}")
    return {
        "task_id": promoted_task_id(goal_id),
        "goal": task_input,
        "expected_output": answer[:MAX_TEXT],
        "expected_tools": _tools_called(events)[:MAX_EXPECTED_ITEMS] if include_tools else [],
        "expected_citations": (
            cited_sources(events)[:MAX_EXPECTED_ITEMS] if include_citations else []
        ),
        "expected_output_contains": _dedupe(expected_output_contains or [])[:MAX_EXPECTED_ITEMS],
        "forbidden_tools": [],
        "min_score": float(min_score),
        "max_iterations": int(max_iterations),
        "tags": _dedupe([*(tags or []), *auto_tags])[:MAX_EXPECTED_ITEMS],
        "source_goal_id": goal_id,
    }
