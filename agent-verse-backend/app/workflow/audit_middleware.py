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
            from app.tenancy.context import PlanTier, TenantContext

            event = AuditEvent(
                goal_id=state.get("run_id", ""),
                tool_name=f"workflow.{event_type}",
                action_level=ActionLevel.ALLOW_LOG,
                outcome=event_type,
                step_id=str(data.get("step_id", "") or "")[:64],
                note=_note(data),
            )
            # AuditLog.record(event, *, tenant_ctx=...). It used to be called as
            # record(tenant_id, event): every emit raised TypeError, was
            # swallowed below, and no workflow audit event was ever written.
            self._audit.record(
                event,
                tenant_ctx=TenantContext(
                    tenant_id=str(state.get("tenant_id", "") or ""),
                    plan=PlanTier.FREE,
                    api_key_id="workflow-engine",
                ),
            )
        except Exception as exc:
            _log.warning("auto_audit_emit_failed", event_type=event_type, error=str(exc))


def _note(data: dict[str, Any]) -> str:
    """Compact ``k=v`` note (hashes / ids only — never raw step payloads)."""
    parts = [f"{k}={v}" for k, v in data.items() if v not in (None, "") and k != "step_id"]
    return "; ".join(parts)[:1000]


def record_workflow_action(
    request: Any,
    action: str,
    *,
    workflow_id: str,
    outcome: str = "success",
    note: str = "",
    approver: str | None = None,
    step_id: str = "",
) -> None:
    """Audit one workflow API action (create/update/publish/run/approve ...).

    Written as ``tool_name="workflow.<action>"`` with ``goal_id=<workflow_id>``
    under the CALLER's tenant (tenant-scoped like every audit row), carrying the
    caller's API key, IP, user agent and request id. Auditing never breaks the
    call path (AuditLog persistence is fire-and-forget by design).
    """
    audit_log = getattr(request.app.state, "audit_log", None)
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        tenant = getattr(request.app.state, "tenant_context", None)
    if audit_log is None or tenant is None:
        return
    try:
        from app.governance.audit import AuditEvent

        headers = getattr(request, "headers", {}) or {}
        client = getattr(request, "client", None)
        key_id = str(getattr(tenant, "api_key_id", "") or "") or None
        audit_log.record(
            AuditEvent(
                goal_id=str(workflow_id)[:64],  # audit_log.goal_id is VARCHAR(64)
                tool_name=f"workflow.{action}",
                action_level=(
                    ActionLevel.DENY if outcome == "denied" else ActionLevel.ALLOW_LOG
                ),
                outcome=outcome[:100],
                step_id=step_id[:64],
                approver=approver,
                note=note[:1000],
                ip_address=getattr(client, "host", None),
                user_agent=headers.get("user-agent"),
                api_key_id=key_id,
                request_id=headers.get("x-request-id"),
            ),
            tenant_ctx=tenant,
        )
    except Exception as exc:  # auditing must never break the call path
        _log.warning("workflow_action_audit_failed", action=action, error=str(exc))


def _created_id(result: Any) -> str:
    """The new workflow id from a create result (dict or model)."""
    if isinstance(result, dict):
        return str(result.get("id") or "")
    return str(getattr(result, "id", "") or "")


def record_workflow_created(
    request: Any,
    result: Any,
    *,
    name: str,
    source: str,
    **detail: str,
) -> None:
    """P4-1: the ONE ``workflow.created`` audit row every create path writes.

    Create-via-API, YAML import, clone, template instantiate/fork and the NL
    builder's save must all leave the same row (``goal_id=<new workflow id>``,
    caller's tenant and key) so the audit trail never depends on which door a
    workflow came through. ``source`` names the door; ``detail`` adds origin ids
    (``cloned_from``, ``template``).
    """
    workflow_id = _created_id(result)
    if not workflow_id:
        _log.warning("workflow_created_audit_missing_id", source=source)
        return
    parts = [f"name={name}", f"source={source}"]
    parts += [f"{k}={v}" for k, v in detail.items() if v]
    record_workflow_action(request, "created", workflow_id=workflow_id, note="; ".join(parts))
