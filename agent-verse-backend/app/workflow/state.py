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


def _keep_max(a: float | int | None, b: float | int | None) -> float | int:
    """Combine cumulative telemetry counters. Step nodes return the running total
    (base + delta), so the latest/highest value is the correct accumulated one;
    max is exact for sequential runs and a safe approximation under concurrency."""
    return max(a or 0, b or 0)


class WorkflowRunStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_HITL = "waiting_hitl"
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
    status: WorkflowRunStatus
    current_step_id: str | None
    completed_branch: str | None  # last conditional branch taken
    error: str | None
    error_step_id: str | None

    # ── HITL ─────────────────────────────────────────────────────────────
    hitl_request_id: str | None  # pending HITLWorkflowRequest UUID
    hitl_action: str | None  # chosen action id
    hitl_note: str | None
    hitl_reviewer: str | None
    hitl_form_data: dict[str, Any] | None  # custom form submission

    # ── foreach progress ─────────────────────────────────────────────────
    # step_id → {"current": N, "total": M, "failed": K}
    foreach_progress: Annotated[dict[str, dict[str, int]], _merge_dict]

    # ── Telemetry ────────────────────────────────────────────────────────
    cost_usd: Annotated[float, _keep_max]
    tokens_used: Annotated[int, _keep_max]
    step_timings: Annotated[dict[str, int], _merge_dict]  # step_id → duration_ms

    # ── Operator control ─────────────────────────────────────────────────
    paused_by: str | None  # user_id who operator-paused
    paused_at: str | None  # ISO timestamp
    pause_reason: str | None

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
