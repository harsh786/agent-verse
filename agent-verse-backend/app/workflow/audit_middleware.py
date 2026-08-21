"""AutoAuditMiddleware — automatically emits AuditEvents for every step transition.

Framework responsibility: no workflow YAML needs an audit.record step.
All step transitions are logged with hash-only output (PII-safe).

Events emitted:
  workflow.run_started    — run begins
  step.started            — step begins executing
  step.completed          — step succeeded
  step.failed             — step raised an exception
  step.skipped            — step skipped (on_failure: skip)
  hitl.requested          — HITL gate created
  hitl.decided            — HITL reviewer submitted decision
  workflow.completed      — run finished successfully
  workflow.failed         — run finished with error
  workflow.paused         — operator paused
  workflow.resumed        — operator resumed
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.governance.permissions import ActionLevel
from app.observability.logging import get_logger
from app.workflow.state import WorkflowState

_log = get_logger(__name__)


def _hash(obj: Any) -> str:
    """SHA-256 hash of a JSON-serialised object (PII-safe audit)."""
    try:
        raw = json.dumps(obj, default=str, sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()[:16]
    except Exception:
        return "unhashable"


class AutoAuditMiddleware:
    """Wraps every step node execution with automatic AuditLog writes."""

    def __init__(self, audit_log: Any) -> None:
        self._audit = audit_log

    # ── Lifecycle events ─────────────────────────────────────────────────

    def run_started(self, state: WorkflowState) -> None:
        self._emit(
            state,
            "workflow.run_started",
            {
                "workflow_id": state.get("workflow_id"),
                "trigger_type": (state.get("inputs") or {}).get("_trigger_type"),
                "inputs_hash": _hash(state.get("inputs")),
            },
        )

    def step_started(self, state: WorkflowState, step_id: str, step_type: str) -> None:
        self._emit(
            state,
            "step.started",
            {
                "step_id": step_id,
                "step_type": step_type,
            },
        )

    def step_completed(
        self,
        state: WorkflowState,
        step_id: str,
        output: Any,
        duration_ms: int,
        cost_usd: float,
    ) -> None:
        self._emit(
            state,
            "step.completed",
            {
                "step_id": step_id,
                "output_hash": _hash(output),
                "duration_ms": duration_ms,
                "cost_usd": cost_usd,
            },
        )

    def step_failed(self, state: WorkflowState, step_id: str, error: str) -> None:
        self._emit(
            state,
            "step.failed",
            {
                "step_id": step_id,
                "error": error[:256],  # truncate long traces
            },
        )

    def step_skipped(self, state: WorkflowState, step_id: str, reason: str) -> None:
        self._emit(
            state,
            "step.skipped",
            {
                "step_id": step_id,
                "reason": reason,
            },
        )

    def hitl_requested(
        self,
        state: WorkflowState,
        step_id: str,
        request_id: str,
        assignee_role: str,
        deadline_at: str,
    ) -> None:
        self._emit(
            state,
            "hitl.requested",
            {
                "step_id": step_id,
                "request_id": request_id,
                "assignee_role": assignee_role,
                "deadline_at": deadline_at,
            },
        )

    def hitl_decided(
        self,
        state: WorkflowState,
        step_id: str,
        action: str,
        reviewer_id: str,
    ) -> None:
        self._emit(
            state,
            "hitl.decided",
            {
                "step_id": step_id,
                "action": action,
                "reviewer_id": reviewer_id,
            },
        )

    def run_completed(
        self,
        state: WorkflowState,
        outputs_hash: str,
        total_cost_usd: float,
        duration_ms: int,
    ) -> None:
        self._emit(
            state,
            "workflow.completed",
            {
                "outputs_hash": outputs_hash,
                "total_cost_usd": total_cost_usd,
                "duration_ms": duration_ms,
            },
        )

    def run_failed(
        self,
        state: WorkflowState,
        error_code: str,
        failed_step_id: str | None,
    ) -> None:
        self._emit(
            state,
            "workflow.failed",
            {
                "error_code": error_code,
                "failed_step_id": failed_step_id,
            },
        )

    def run_paused(self, state: WorkflowState, paused_by: str, reason: str) -> None:
        self._emit(
            state,
            "workflow.paused",
            {
                "paused_by": paused_by,
                "reason": reason,
            },
        )

    def run_resumed(self, state: WorkflowState, resumed_by: str) -> None:
        self._emit(state, "workflow.resumed", {"resumed_by": resumed_by})

    # ── Internal ─────────────────────────────────────────────────────────

    def _emit(
        self,
        state: WorkflowState,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        try:
            from app.governance.audit import AuditEvent  # local import avoids circulars

            event = AuditEvent(
                goal_id=state.get("run_id", ""),
                tool_name=f"workflow.{event_type}",
                action_level=ActionLevel.ALLOW_LOG,
                outcome=event_type,
                step_id=data.get("step_id", ""),
            )
            self._audit.record(state.get("tenant_id", ""), event)
        except Exception as exc:
            _log.warning("auto_audit_emit_failed", event_type=event_type, error=str(exc))
