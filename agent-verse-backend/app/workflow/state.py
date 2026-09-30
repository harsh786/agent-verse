"""WorkflowState — the typed LangGraph graph state for workflow execution.

All fields are serializable for Redis checkpointing (LangGraph MemorySaver
or AsyncRedisSaver). The state flows through every step node and is merged
with each node's returned dict (LangGraph reducer pattern).
"""

from __future__ import annotations

import enum
from typing import Annotated, Any, TypedDict

# ── State reducers ────────────────────────────────────────────────────────────
# LangGraph runs steps with no unmet dependency (and every `parallel` branch)
# CONCURRENTLY. When two concurrent nodes write the same state key, LangGraph
# requires a reducer to merge the updates — without one it raises
# InvalidUpdateError ("Can receive only one value per step"). These reducers make
# the accumulator keys merge cleanly so fan-out / parallel workflows work.


def _merge_dict(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    """Shallow-merge two accumulator dicts (later keys win)."""
    if not a:
        return b or {}
    if not b:
        return a
    return {**a, **b}


def _add(a: float | int | None, b: float | int | None) -> float | int:
    """Sum telemetry deltas. Step nodes return the DELTA their step incurred (not a
    running total), so concurrent steps each contribute their full cost/tokens —
    accounting stays exact under fan-out/parallel (a max reducer would undercount
    concurrent spend and let a tenant slip past cost/token budgets)."""
    return (a or 0) + (b or 0)


def _last_write(a: Any, b: Any) -> Any:
    """Run-control keys: the most recent write wins, and ``None`` is a real value.

    Sequentially this is exactly the plain-channel behaviour (HITL resume moves
    WAITING_HITL -> RUNNING, a fresh re-dispatch clears ``paused_by`` to None).
    Its purpose is concurrency: after a step fails (default ``on_failure: pause``)
    every step of the next superstep sees ``paused_by`` and also writes
    ``status: PAUSED``; with no reducer two such writes raised InvalidUpdateError
    and the run died with a LangGraph message instead of pausing on the step that
    failed. Concurrent writers here write the same halt, so order is immaterial.
    """
    return b


class WorkflowRunControlSignal(Exception):  # noqa: N818
    """Base for cooperative run-control halts raised between steps.

    Deliberately NOT named ``...Error``: this is a cooperative control-flow
    signal, not a failure. Cancel and pause are operator actions on a healthy
    run, and naming them errors would invite callers to treat a normal
    lifecycle transition as a fault (and to log/alert on it). The stdlib draws
    the same distinction with ``StopIteration`` and ``GeneratorExit``, which
    likewise carry no ``Error`` suffix. N818 is suppressed here rather than
    renamed for that reason.
    """


class WorkflowCancelled(WorkflowRunControlSignal):
    """An operator cancelled the run (via the API); abort remaining steps."""


class WorkflowPaused(WorkflowRunControlSignal):
    """An operator paused the run (via the API); halt, keeping progress."""


class WorkflowRunStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_HITL = "waiting_hitl"
    # A durable timer ``wait`` step suspended the run: the wake time is persisted
    # (workflow_runs.wake_at) and a beat scan re-dispatches the run once it passes.
    WAITING_TIMER = "waiting_timer"
    PAUSED = "paused"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class StepStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowState(TypedDict, total=False):
    # ── Identity ──────────────────────────────────────────────────────────
    run_id: str
    workflow_id: str
    tenant_id: str
    workflow_name: str

    # ── Inputs & outputs ─────────────────────────────────────────────────
    inputs: dict[str, Any]  # trigger / API inputs (after trigger_transform)
    raw_trigger: dict[str, Any]  # raw payload before trigger_transform
    # step_outputs: merged so concurrent/parallel steps don't collide (see reducers).
    step_outputs: Annotated[dict[str, Any], _merge_dict]  # step_id → output dict
    outputs: dict[str, Any]  # final workflow-level outputs

    # ── Mutable variables (set_variable steps) ───────────────────────────
    vars: Annotated[dict[str, Any], _merge_dict]  # read via {{vars.X}}

    # ── Execution state ───────────────────────────────────────────────────
    # Run-control keys use _last_write so parallel steps can settle together.
    status: Annotated[WorkflowRunStatus, _last_write]
    current_step_id: Annotated[str | None, _last_write]
    completed_branch: Annotated[str | None, _last_write]  # last conditional branch taken
    error: Annotated[str | None, _last_write]
    error_step_id: Annotated[str | None, _last_write]

    # ── HITL ─────────────────────────────────────────────────────────────
    hitl_request_id: Annotated[str | None, _last_write]  # pending HITLWorkflowRequest UUID
    hitl_action: Annotated[str | None, _last_write]  # chosen action id
    hitl_note: Annotated[str | None, _last_write]
    hitl_reviewer: Annotated[str | None, _last_write]
    hitl_form_data: Annotated[dict[str, Any] | None, _last_write]  # custom form submission

    # ── foreach progress ─────────────────────────────────────────────────
    # step_id → {"current": N, "total": M, "failed": K}
    foreach_progress: Annotated[dict[str, dict[str, int]], _merge_dict]

    # ── Telemetry ────────────────────────────────────────────────────────
    cost_usd: Annotated[float, _add]
    tokens_used: Annotated[int, _add]
    step_timings: Annotated[dict[str, int], _merge_dict]  # step_id → duration_ms

    # ── Operator control ─────────────────────────────────────────────────
    paused_by: Annotated[str | None, _last_write]  # user_id who operator-paused
    paused_at: Annotated[str | None, _last_write]  # ISO timestamp
    pause_reason: Annotated[str | None, _last_write]

    # ── Test run ─────────────────────────────────────────────────────────
    is_test_run: bool
    mock_overrides: dict[str, Any]  # step_id → mock output

    # ── Labels & metadata ────────────────────────────────────────────────
    labels: dict[str, str]
    run_metadata: dict[str, Any]

    # ── Vault refs used (for SecretMasker) ───────────────────────────────
    vault_refs_used: set[str]  # vault key names resolved this run

    # ── Environment (workflow.env DSL section) ────────────────────────────
    _env: dict[str, str]  # resolved from definition.env
