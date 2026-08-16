"""WorkflowState — the typed LangGraph graph state for workflow execution.

All fields are serializable for Redis checkpointing (LangGraph MemorySaver
or AsyncRedisSaver). The state flows through every step node and is merged
with each node's returned dict (LangGraph reducer pattern).
"""
from __future__ import annotations

import enum
from typing import Any, TypedDict


class WorkflowRunStatus(enum.StrEnum):
    PENDING      = "pending"
    RUNNING      = "running"
    WAITING_HITL = "waiting_hitl"
    PAUSED       = "paused"
    COMPLETE     = "complete"
    FAILED       = "failed"
    CANCELLED    = "cancelled"
    TIMED_OUT    = "timed_out"


class StepStatus(enum.StrEnum):
    PENDING  = "pending"
    RUNNING  = "running"
    COMPLETE = "complete"
    FAILED   = "failed"
    SKIPPED  = "skipped"


class WorkflowState(TypedDict, total=False):
    # ── Identity ──────────────────────────────────────────────────────────
    run_id:          str
    workflow_id:     str
    tenant_id:       str
    workflow_name:   str

    # ── Inputs & outputs ─────────────────────────────────────────────────
    inputs:          dict[str, Any]   # trigger / API inputs (after trigger_transform)
    raw_trigger:     dict[str, Any]   # raw payload before trigger_transform
    step_outputs:    dict[str, Any]   # step_id → output dict (immutable after step)
    outputs:         dict[str, Any]   # final workflow-level outputs

    # ── Mutable variables (set_variable steps) ───────────────────────────
    vars:            dict[str, Any]   # read via {{vars.X}}

    # ── Execution state ───────────────────────────────────────────────────
    status:           WorkflowRunStatus
    current_step_id:  str | None
    completed_branch: str | None      # last conditional branch taken
    error:            str | None
    error_step_id:    str | None

    # ── HITL ─────────────────────────────────────────────────────────────
    hitl_request_id:  str | None      # pending HITLWorkflowRequest UUID
    hitl_action:      str | None      # chosen action id
    hitl_note:        str | None
    hitl_reviewer:    str | None
    hitl_form_data:   dict[str, Any] | None  # custom form submission

    # ── foreach progress ─────────────────────────────────────────────────
    # step_id → {"current": N, "total": M, "failed": K}
    foreach_progress: dict[str, dict[str, int]]

    # ── Telemetry ────────────────────────────────────────────────────────
    cost_usd:         float
    tokens_used:      int
    step_timings:     dict[str, int]  # step_id → duration_ms

    # ── Operator control ─────────────────────────────────────────────────
    paused_by:        str | None      # user_id who operator-paused
    paused_at:        str | None      # ISO timestamp
    pause_reason:     str | None

    # ── Test run ─────────────────────────────────────────────────────────
    is_test_run:      bool
    mock_overrides:   dict[str, Any]  # step_id → mock output

    # ── Labels & metadata ────────────────────────────────────────────────
    labels:           dict[str, str]
    run_metadata:     dict[str, Any]

    # ── Vault refs used (for SecretMasker) ───────────────────────────────
    vault_refs_used:  set[str]        # vault key names resolved this run

    # ── Environment (workflow.env DSL section) ────────────────────────────
    _env:             dict[str, str]  # resolved from definition.env
