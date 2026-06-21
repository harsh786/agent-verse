"""Immutable audit trail — append-only log of all governed actions.

Records are stored per-tenant and are queryable by goal_id, tool_name, or
date range. The append-only constraint is enforced structurally: there is
no delete or update method.

In production this is backed by an append-only PostgreSQL table with an
immutability trigger; this in-memory version is used in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.governance.permissions import ActionLevel
from app.tenancy.context import TenantContext


@dataclass
class AuditEvent:
    goal_id: str
    tool_name: str
    action_level: ActionLevel
    outcome: str
    step_id: str = ""
    approver: str | None = None
    note: str = ""
    event_id: str = field(default_factory=lambda: __import__("uuid").uuid4().hex)


class AuditLog:
    """Append-only in-memory audit log, namespaced per tenant."""

    def __init__(self) -> None:
        self._log: dict[str, list[AuditEvent]] = {}

    def record(self, event: AuditEvent, *, tenant_ctx: TenantContext) -> None:
        self._log.setdefault(tenant_ctx.tenant_id, []).append(event)

    def query(
        self,
        *,
        tenant_ctx: TenantContext,
        goal_id: str | None = None,
        tool_name: str | None = None,
    ) -> list[AuditEvent]:
        events = self._log.get(tenant_ctx.tenant_id, [])
        if goal_id is not None:
            events = [e for e in events if e.goal_id == goal_id]
        if tool_name is not None:
            events = [e for e in events if e.tool_name == tool_name]
        return list(events)
