"""Workflow ENGINE lifecycle audit (WF-ENGINE-AUDIT).

Run and step lifecycle events are written to ``audit_log`` by the run store,
inside the SAME transaction as the status write that causes them:

* durable — no fire-and-forget task that dies with the worker's event loop
  (``AuditLog.record`` schedules one; in a Celery worker the loop is torn down
  right after the run, so those rows were simply lost);
* consistent — an event exists exactly when its status change committed;
* non-blocking — the insert runs in a SAVEPOINT, so an audit failure is logged
  and rolled back alone; it never fails or delays the run beyond one INSERT.

Rows are tenant-scoped like every audit row (``audit_log.tenant_id`` + RLS) and
keyed by ``goal_id = <run_id>``, so ``GET /governance/audit?goal_id=<run_id>``
returns a run's trail. Only ids, statuses and (truncated) errors are recorded —
never step inputs/outputs.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

ENGINE_ACTOR = "workflow-engine"

# New run status -> audit event (``workflow.run.<event>``). ``running`` is
# resolved to started/resumed from the previous status.
_RUN_EVENTS: dict[str, str] = {
    "paused": "paused",
    "waiting_hitl": "waiting_approval",
    "waiting_timer": "waiting_timer",
    "complete": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "timed_out": "timed_out",
}

# Step result status -> audit event (``workflow.step.<event>``).
_STEP_EVENTS: dict[str, str] = {
    "running": "started",
    "complete": "completed",
    "failed": "failed",
    "skipped": "skipped",
    "waiting_hitl": "waiting_approval",
}


def run_event(old_status: str | None, new_status: str) -> str | None:
    """The run lifecycle event for ``old_status -> new_status`` (None = no event)."""
    if old_status == new_status:
        return None
    if new_status == "running":
        return "started" if old_status in (None, "pending") else "resumed"
    return _RUN_EVENTS.get(new_status)


def step_event(status: str) -> str | None:
    return _STEP_EVENTS.get(status)


def audit_tenant_id(tenant_id: str) -> str:
    """``audit_log.tenant_id`` is the 32-char hex form tenants are created with
    (and what ``GET /governance/audit`` filters on); the workflow tables store a
    UUID, so a dashed id is normalised."""
    try:
        return uuid.UUID(str(tenant_id)).hex
    except ValueError:
        return str(tenant_id)


async def write_engine_audit(
    session: Any,
    *,
    tenant_id: str,
    run_id: str,
    kind: str,
    event: str,
    step_id: str = "",
    note: str = "",
) -> None:
    """Insert one ``workflow.<kind>.<event>`` row in the caller's transaction.

    Runs in a SAVEPOINT: on failure only the audit insert is rolled back and the
    caller's status write still commits.
    """
    from sqlalchemy import text as sa_text

    from app.governance.audit import check_audit_widths

    tid = audit_tenant_id(tenant_id)
    try:
        # Ids are never truncated (P4-2): an overflow is refused and logged.
        check_audit_widths({"tenant_id": tid, "goal_id": str(run_id), "step_id": step_id or ""})
        async with session.begin_nested():
            # audit_log's policy compares tenant_id to app.tenant_id as TEXT, so
            # the GUC must carry the same (hex) form as the row. The workflow
            # tables' policies parse the GUC as a UUID, so this changes nothing
            # for them.
            await session.execute(
                sa_text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tid}
            )
            await session.execute(
                sa_text(
                    "INSERT INTO audit_log (id, tenant_id, goal_id, tool_name, action_level, "
                    " outcome, step_id, note, api_key_id, created_at) "
                    "VALUES (:id, :tid, :gid, :tool, 'allow_log', :outcome, :step, :note, "
                    " :actor, NOW())"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "tid": tid,
                    "gid": str(run_id),
                    "tool": f"workflow.{kind}.{event}",
                    "outcome": event[:100],
                    "step": step_id or "",
                    "note": note[:1000],
                    "actor": ENGINE_ACTOR,
                },
            )
    except Exception as exc:  # auditing must never fail the run
        _log.warning(
            "workflow_engine_audit_failed",
            run_id=run_id,
            audit_event=f"{kind}.{event}",
            error=str(exc)[:200],
        )


def note_of(**fields: Any) -> str:
    """Compact ``k=v`` note, skipping empty values."""
    return "; ".join(f"{k}={v}" for k, v in fields.items() if v not in (None, ""))
