"""Immutable audit trail — append-only log of all governed actions.

Records are stored per-tenant and are queryable by goal_id, tool_name, or
date range. The append-only constraint is enforced structurally: there is
no delete or update method.

In production this is backed by an append-only PostgreSQL table with an
immutability trigger; this in-memory version is used in tests.

When ``db_session_factory`` is supplied, writes are also persisted to
PostgreSQL via fire-and-forget asyncio tasks. DB failures are logged as
warnings and never raised to callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.governance.permissions import ActionLevel
from app.observability.logging import get_logger
from app.tenancy.context import TenantContext

_log = get_logger(__name__)


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
    # SOC2-required fields
    ip_address: str | None = None
    user_agent: str | None = None
    api_key_id: str | None = None
    request_id: str | None = None
    connector_id: str | None = None
    auth_type: str | None = None


class AuditLog:
    """Append-only in-memory audit log, namespaced per tenant.

    When ``db_session_factory`` is provided, records are also persisted to
    PostgreSQL via fire-and-forget asyncio tasks. DB failures are logged as
    warnings and never raised to callers.
    """

    def __init__(self, db_session_factory: Any = None) -> None:
        self._log: dict[str, list[AuditEvent]] = {}
        self._db = db_session_factory

    def record(self, event: AuditEvent, *, tenant_ctx: TenantContext) -> None:
        """Record in memory, then fire-and-forget to DB."""
        self._log.setdefault(tenant_ctx.tenant_id, []).append(event)
        if self._db is not None:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._db_record(event, tenant_ctx.tenant_id))
            except RuntimeError:
                pass  # No running loop (e.g., in sync test context)

    async def _db_record(self, event: AuditEvent, tenant_id: str) -> None:
        if self._db is None:
            return
        try:
            from app.db.models.governance import AuditLog as AuditLogModel
            from app.db.rls import sqlalchemy_rls_context
            async with self._db() as session, session.begin():
                async with sqlalchemy_rls_context(session, tenant_id):
                    row = AuditLogModel(
                        id=event.event_id,
                        tenant_id=tenant_id,
                        goal_id=event.goal_id,
                        tool_name=event.tool_name,
                        action_level=event.action_level.value,
                        outcome=event.outcome,
                        step_id=event.step_id or "",
                        approver=event.approver,
                        note=event.note,
                        ip_address=event.ip_address,
                        user_agent=event.user_agent,
                        api_key_id=event.api_key_id,
                        request_id=event.request_id,
                        connector_id=event.connector_id,
                    )
                    session.add(row)
        except Exception as exc:
            _log.warning("DB audit record failed: %s", exc)

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

    async def sync_from_db(self, *, tenant_id: str | None = None) -> int:
        """Load audit entries from PostgreSQL into memory.

        Returns the number of new entries loaded (deduplicates by event_id).
        Returns 0 immediately when no ``db_session_factory`` is configured.
        """
        if self._db is None:
            return 0
        try:
            from sqlalchemy import select

            from app.db.models.governance import AuditLog as AuditLogModel

            loaded = 0
            async with self._db() as session:
                q = select(AuditLogModel)
                if tenant_id:
                    q = q.where(AuditLogModel.tenant_id == tenant_id)
                result = await session.execute(q)
                rows = result.scalars().all()
                for row in rows:
                    events = self._log.setdefault(row.tenant_id, [])
                    # Avoid duplicates
                    existing_ids = {e.event_id for e in events}
                    if row.id not in existing_ids:
                        try:
                            level = ActionLevel(row.action_level)
                        except ValueError:
                            level = ActionLevel.ALLOW_LOG
                        evt = AuditEvent(
                            goal_id=row.goal_id,
                            tool_name=row.tool_name,
                            action_level=level,
                            outcome=row.outcome,
                            step_id=row.step_id or "",
                            approver=row.approver,
                            note=row.note or "",
                            event_id=row.id,
                        )
                        events.append(evt)
                        loaded += 1
            return loaded
        except Exception as exc:
            _log.warning("DB audit sync failed: %s", exc)
            return 0
