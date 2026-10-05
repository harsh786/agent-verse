"""Step-checkpoint payloads for crash-safe goal resume.

A goal re-delivered after a worker crash (Celery ``acks_late`` redelivery or the
stuck-goal recovery sweep) must not re-run tools it already ran — they can have
side effects (a ticket filed, a message sent). After each finished step the
executor persists the executable plan plus the outputs of the completed steps;
on resume the planner reuses that plan and the executor skips those steps.

Payloads are plain JSON (they live in ``goal_checkpoints.payload``).
"""

from __future__ import annotations

import json
from typing import Any

from app.agent.goal_action_ledger import LEDGER_CONTEXT_KEY
from app.agent.state import AgentState, StepResult, StepStatus
from app.tenancy.context import TenantContext

CHECKPOINT_VERSION = 2
# Keys on ``AgentState.context`` shared by the planner, executor and resume path.
EXECUTABLE_PLAN_KEY = "_executable_plan"
COMPLETED_STEPS_KEY = "_ckpt_done"
RESUME_PLAN_KEY = "_resume_plan"
RESUME_COMPLETED_KEY = "_resume_completed"

_MAX_OUTPUT_CHARS = 8000


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def checkpoint_payload(state: Any, step_index: int) -> dict[str, Any]:
    """Build the JSON payload persisted after a finished step."""
    context = getattr(state, "context", None) or {}
    steps = [
        {
            "step_id": s.step_id,
            "description": s.description,
            "status": str(s.status),
            "output": (s.output or "")[:_MAX_OUTPUT_CHARS],
            "error": s.error,
            "tool_calls": _json_safe(s.tool_calls),
        }
        for s in getattr(state, "steps", []) or []
        if isinstance(s, StepResult)
    ]
    completed = {
        str(k): str(v)[:_MAX_OUTPUT_CHARS]
        for k, v in (context.get(COMPLETED_STEPS_KEY) or {}).items()
    }
    return {
        "version": CHECKPOINT_VERSION,
        "step_index": step_index,
        "iterations": int(getattr(state, "iterations", 0) or 0),
        "plan": [str(p) for p in getattr(state, "plan", []) or []],
        "executable_plan": [str(p) for p in context.get(EXECUTABLE_PLAN_KEY) or []],
        "completed": completed,
        "steps": steps,
        # OI-1: executed side-effecting calls + approval decisions of this goal,
        # so a resumed run neither repeats a call nor re-asks an approval.
        "action_ledger": _json_safe(context.get(LEDGER_CONTEXT_KEY) or {}),
    }


def restore_from_checkpoint(
    payload: dict[str, Any] | None,
    seed: AgentState | None,
    *,
    goal: str,
    tenant_ctx: TenantContext,
    goal_id: str,
) -> AgentState | None:
    """Rebuild an ``AgentState`` that resumes after the checkpointed steps.

    Returns ``None`` when there is nothing to resume: no checkpoint, a legacy
    payload without a plan, or no completed step yet.
    """
    if not isinstance(payload, dict) or payload.get("version") != CHECKPOINT_VERSION:
        return None
    executable_plan = [str(p) for p in payload.get("executable_plan") or []]
    completed = {str(k): str(v) for k, v in (payload.get("completed") or {}).items()}
    if not executable_plan or not completed:
        return None

    state = seed if isinstance(seed, AgentState) else AgentState(goal=goal, tenant_ctx=tenant_ctx)
    state.goal_id = goal_id
    state.iterations = int(payload.get("iterations") or 0)
    state.plan = [str(p) for p in payload.get("plan") or []]
    restored: list[StepResult] = []
    for raw in payload.get("steps") or []:
        if not isinstance(raw, dict):
            continue
        try:
            status = StepStatus(raw.get("status", StepStatus.PENDING))
        except ValueError:
            status = StepStatus.FAILED
        # A step that was mid-flight when the worker died did not finish.
        if status in (StepStatus.PENDING, StepStatus.RUNNING):
            status = StepStatus.FAILED
        restored.append(
            StepResult(
                step_id=str(raw.get("step_id") or ""),
                description=str(raw.get("description") or ""),
                status=status,
                output=str(raw.get("output") or ""),
                tool_calls=list(raw.get("tool_calls") or []),
                error=raw.get("error"),
            )
        )
    state.steps = restored
    state.context[RESUME_PLAN_KEY] = executable_plan
    state.context[RESUME_COMPLETED_KEY] = dict(completed)
    state.context[COMPLETED_STEPS_KEY] = dict(completed)
    state.context[EXECUTABLE_PLAN_KEY] = executable_plan
    ledger = payload.get("action_ledger")
    if isinstance(ledger, dict) and ledger:
        state.context[LEDGER_CONTEXT_KEY] = ledger
    return state
