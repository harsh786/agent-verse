"""Persistence adapter — writes critical events to PostgreSQL when DB is available.

Design: always use the in-memory services (fast, testable), and additionally
attempt to persist to PostgreSQL in the background. Failure to persist is
logged but never raises to the caller (in-memory is source of truth for now).
"""
from __future__ import annotations

import logging
from datetime import UTC
from typing import Any

from opentelemetry import trace

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer(__name__)


async def persist_goal(
    *,
    goal_id: str,
    tenant_id: str,
    goal_text: str,
    status: str,
    priority: str,
    dry_run: bool,
    db_session_factory: Any = None,
) -> None:
    """Write goal to PostgreSQL in the background. No-op if session_factory is None."""
    if db_session_factory is None:
        return
    try:
        from app.db.models.goal import Goal

        async with db_session_factory() as session:
            goal = Goal(
                id=goal_id,
                tenant_id=tenant_id,
                goal_text=goal_text,
                status=status,
                priority=priority,
                dry_run=dry_run,
            )
            session.add(goal)
            await session.commit()
    except Exception as exc:
        logger.warning("Failed to persist goal %s to DB: %s", goal_id, exc)


async def persist_goal_status(
    *,
    goal_id: str,
    tenant_id: str,
    status: str,
    error_message: str = "",
    db_session_factory: Any = None,
) -> None:
    """Update goal status in PostgreSQL. No-op if session_factory is None."""
    if db_session_factory is None:
        return
    try:
        from datetime import datetime

        from sqlalchemy import update

        from app.db.models.goal import Goal

        async with db_session_factory() as session:
            await session.execute(
                update(Goal)
                .where(Goal.id == goal_id, Goal.tenant_id == tenant_id)
                .values(
                    status=status,
                    error_message=error_message,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
    except Exception as exc:
        logger.warning("Failed to update goal %s status to DB: %s", goal_id, exc)


async def persist_audit_event(
    *,
    goal_id: str,
    tool_name: str,
    action_level: str,
    outcome: str,
    step_id: str,
    tenant_id: str,
    db_session_factory: Any = None,
) -> None:
    """Write audit event to PostgreSQL. No-op if session_factory is None."""
    if db_session_factory is None:
        return
    try:
        import uuid

        from app.db.models.governance import AuditLog as AuditLogModel

        async with db_session_factory() as session:
            entry = AuditLogModel(
                id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                goal_id=goal_id,
                tool_name=tool_name,
                action_level=action_level,
                outcome=outcome,
                step_id=step_id,
            )
            session.add(entry)
            await session.commit()
    except Exception as exc:
        logger.warning("Failed to persist audit event to DB: %s", exc)
