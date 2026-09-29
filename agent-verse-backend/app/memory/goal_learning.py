"""Close the Reflexion loop: learn from every terminal goal, score recalled memory.

The planner recalls evidence-backed lessons (``ReflexionService.recall``) and
records the ids it injected into ``AgentState.context[RECALLED_MEMORY_IDS_KEY]``.
Nothing used to *write* them, so recall only ever saw an empty store. After a
goal reaches a terminal state (complete / failed) both execution paths — the
API's in-process runner (``GoalService``) and the Celery worker (``run_goal``)
— call :func:`learn_from_goal_outcome`, which:

1. records effectiveness feedback for every memory the plan used (helpful when
   the goal completed; a failure lowers the outcome score but never marks the
   memory *harmful* — one failure is not evidence the lesson caused it);
2. derives a lesson and stores it with ``ReflexionService.learn``.

The lesson is built from the goal's own evidence: the verifier / reflection
LLM's feedback (the existing Reflexion lesson path, ``ReflexionWirer``) when
the run produced one, otherwise a deterministic outcome record (status,
completed/failed steps, tools, error) — never nothing. It is screened by the
same MEMORY_WRITE guardrail long-term memory uses, then written under the
tenant's RLS by the repository, which quarantines evidence-less or
poisoned content.

Governance: dry runs write nothing; learning is bounded by a timeout and never
fails or delays the goal's completion — every failure is logged and reported
in the returned :class:`GoalLearningResult`, never raised.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

from app.observability.logging import get_logger

RECALLED_MEMORY_IDS_KEY = "_reflexion_memory_ids"
LEARN_TIMEOUT_SECONDS = 15.0
LESSON_SOURCE = "goal_outcome"

_TERMINAL = frozenset({"complete", "failed"})
_MAX_EVIDENCE_STEPS = 4
_MAX_LESSON_CHARS = 1_500

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DerivedLesson:
    content: str
    evidence_refs: tuple[str, ...]
    confidence: int
    outcome: str


@dataclass(frozen=True, slots=True)
class GoalLearningResult:
    status: Literal["learned", "skipped", "failed"]
    reason: str = ""
    memory_id: str | None = None
    feedback_memory_ids: tuple[str, ...] = field(default_factory=tuple)


def _status_value(state: Any) -> str:
    status = getattr(state, "status", "")
    return str(getattr(status, "value", status) or "").lower()


def recalled_memory_ids(state: Any) -> tuple[str, ...]:
    context = getattr(state, "context", None)
    raw = context.get(RECALLED_MEMORY_IDS_KEY) if isinstance(context, dict) else None
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(str(item) for item in raw if item))


def _step_tools(step: Any) -> list[str]:
    tools: list[str] = []
    for call in getattr(step, "tool_calls", None) or []:
        if isinstance(call, dict):
            name = call.get("tool") or call.get("name") or call.get("tool_name")
            if name:
                tools.append(str(name))
    return tools


def derive_goal_lesson(state: Any, *, goal_id: str) -> DerivedLesson | None:
    """Build the lesson for a terminal goal, or None when it is not terminal."""
    outcome = _status_value(state)
    if outcome not in _TERMINAL:
        return None
    goal = " ".join(str(getattr(state, "goal", "") or "").split())[:160]
    steps = list(getattr(state, "steps", None) or [])
    step_status = [(step, str(getattr(step.status, "value", step.status))) for step in steps]
    completed = [step for step, status in step_status if status == "complete"]
    failed = [step for step, status in step_status if status == "failed"]
    tools = list(dict.fromkeys(tool for step in completed for tool in _step_tools(step)))
    iterations = int(getattr(state, "iterations", 0) or 0)
    if outcome == "complete":
        parts = [f"Goal '{goal}' succeeded after {iterations} iteration(s)."]
        if completed:
            parts.append(
                "Approach that worked: "
                + " -> ".join(str(step.description)[:120] for step in completed[:5])
                + "."
            )
        if tools:
            parts.append("Tools used: " + ", ".join(tools[:8]) + ".")
        confidence = 7_000
        evidence_steps = completed
    else:
        from app.agent.reflexion_wirer import ReflexionWirer, _classify_failure

        feedback = (getattr(state, "verification_feedback", "") or "").strip()
        error = (getattr(state, "error_message", "") or "").strip()
        failure_class = _classify_failure(feedback or error)
        # The verifier/reflection LLM's diagnosis, via the existing lesson path.
        llm_lesson = ReflexionWirer().extract_lesson(state) if feedback else None
        parts = [f"Goal '{goal}' failed ({failure_class})."]
        if llm_lesson:
            parts.append(f"Lesson: {llm_lesson}")
        elif error:
            parts.append(f"Error: {error[:300]}")
        for step in failed[:3]:
            parts.append(
                f"Failed step: {str(step.description)[:120]}"
                + (f" — {str(step.error)[:160]}" if getattr(step, "error", None) else "")
            )
        parts.append("Avoid repeating this approach without addressing the cause.")
        confidence = 6_000
        evidence_steps = failed or steps
    evidence = (
        f"goal://{goal_id}/outcome/{outcome}",
        *(
            f"goal://{goal_id}/step/{step.step_id}"
            for step in evidence_steps[:_MAX_EVIDENCE_STEPS]
            if getattr(step, "step_id", None)
        ),
    )
    return DerivedLesson(
        content=" ".join(parts)[:_MAX_LESSON_CHARS],
        evidence_refs=evidence,
        confidence=confidence,
        outcome=outcome,
    )


async def _record_feedback(
    reflexion_service: Any, *, tenant_id: str, goal_id: str, outcome: str, ids: tuple[str, ...]
) -> tuple[str, ...]:
    recorded: list[str] = []
    succeeded = outcome == "complete"
    for memory_id in ids:
        try:
            await reflexion_service.record_effectiveness(
                tenant_id=tenant_id,
                memory_id=memory_id,
                execution_id=goal_id,
                used=True,
                helpful=succeeded,
                harmful=False,
                outcome_score=5_000 if succeeded else -5_000,
                reason=f"recalled into the plan of goal {goal_id}, which ended {outcome}",
            )
            recorded.append(memory_id)
        except KeyError:
            _log.info("goal_learning_feedback_memory_gone", goal_id=goal_id, memory_id=memory_id)
        except Exception as exc:
            _log.warning(
                "goal_learning_feedback_failed",
                goal_id=goal_id,
                memory_id=memory_id,
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
            )
    return tuple(recorded)


async def _learn(
    reflexion_service: Any,
    state: Any,
    *,
    tenant_id: str,
    goal_id: str,
    agent_id: str | None,
) -> GoalLearningResult:
    from app.memory.screening import MemoryScreeningError, screen_memory_content

    state_tenant = getattr(getattr(state, "tenant_ctx", None), "tenant_id", None)
    if state_tenant is not None and state_tenant != tenant_id:
        # Fail closed: never attribute one tenant's run to another tenant.
        _log.error(
            "goal_learning_tenant_mismatch", goal_id=goal_id, tenant_id=tenant_id
        )
        return GoalLearningResult("failed", "tenant_mismatch")
    lesson = derive_goal_lesson(state, goal_id=goal_id)
    if lesson is None:
        return GoalLearningResult("skipped", "not_terminal")
    feedback_ids = await _record_feedback(
        reflexion_service,
        tenant_id=tenant_id,
        goal_id=goal_id,
        outcome=lesson.outcome,
        ids=recalled_memory_ids(state),
    )
    try:
        screened = await screen_memory_content(
            lesson.content, tenant_id=tenant_id, goal_id=goal_id
        )
    except MemoryScreeningError as exc:
        _log.warning("goal_learning_screening_failed_closed", goal_id=goal_id, error=str(exc))
        return GoalLearningResult("failed", "screening_unavailable", None, feedback_ids)
    if screened is None:
        _log.info("goal_learning_blocked_by_guardrail", goal_id=goal_id)
        return GoalLearningResult("skipped", "blocked_by_guardrail", None, feedback_ids)
    record = await reflexion_service.learn(
        tenant_id=tenant_id,
        goal_id=goal_id,
        execution_id=goal_id,
        safe_lesson=screened,
        evidence_refs=lesson.evidence_refs,
        classification="internal",
        confidence=lesson.confidence,
        idempotency_key=f"reflexion:goal:{goal_id}:{lesson.outcome}",
        agent_id=agent_id or None,
        source=LESSON_SOURCE,
    )
    memory_id = str(getattr(record, "memory_id", "") or "") or None
    _log.info(
        "goal_learning_recorded",
        goal_id=goal_id,
        outcome=lesson.outcome,
        memory_id=memory_id,
        lifecycle_state=getattr(record, "lifecycle_state", None),
        feedback_count=len(feedback_ids),
    )
    return GoalLearningResult("learned", lesson.outcome, memory_id, feedback_ids)


async def learn_from_goal_outcome(
    reflexion_service: Any,
    state: Any,
    *,
    tenant_id: str,
    goal_id: str,
    dry_run: bool,
    agent_id: str | None = None,
    timeout_s: float = LEARN_TIMEOUT_SECONDS,
) -> GoalLearningResult:
    """Learn from a terminal goal. Never raises; bounded by ``timeout_s``."""
    if dry_run:
        return GoalLearningResult("skipped", "dry_run")
    if reflexion_service is None:
        _log.info("goal_learning_unavailable", goal_id=goal_id, reason="no_reflexion_service")
        return GoalLearningResult("skipped", "no_reflexion_service")
    if state is None:
        return GoalLearningResult("skipped", "no_final_state")
    try:
        return await asyncio.wait_for(
            _learn(
                reflexion_service,
                state,
                tenant_id=tenant_id,
                goal_id=goal_id,
                agent_id=agent_id,
            ),
            timeout=timeout_s,
        )
    except TimeoutError:
        _log.warning("goal_learning_timed_out", goal_id=goal_id, timeout_s=timeout_s)
        return GoalLearningResult("failed", "timeout")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _log.warning(
            "goal_learning_failed",
            goal_id=goal_id,
            error=f"{type(exc).__name__}: {str(exc)[:300]}",
        )
        return GoalLearningResult("failed", type(exc).__name__)


__all__ = [
    "LEARN_TIMEOUT_SECONDS",
    "RECALLED_MEMORY_IDS_KEY",
    "DerivedLesson",
    "GoalLearningResult",
    "derive_goal_lesson",
    "learn_from_goal_outcome",
    "recalled_memory_ids",
]
