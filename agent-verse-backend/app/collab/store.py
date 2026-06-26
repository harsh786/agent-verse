"""Tenant-scoped collaboration session store."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.db.models.intelligence import CollabOperation, CollabSession
from app.db.rls import sqlalchemy_rls_context
from app.tenancy.context import TenantContext


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _session_to_dict(
    session: CollabSession, participants: list[str] | None = None
) -> dict[str, Any]:
    metadata = session.metadata_json or {}
    session_participants = (
        participants if participants is not None else metadata.get("participants", [])
    )
    return {
        "session_id": session.id,
        "tenant_id": session.tenant_id,
        "name": session.name,
        "mode": session.mode or "suggest",
        "status": session.status or "active",
        "content": session.content or "",
        "goal_id": metadata.get("goal_id"),
        "agent_id": metadata.get("agent_id"),
        "participants": session_participants,
        "participant_count": len(session_participants),
        "created_at": session.created_at.isoformat() if session.created_at else "",
        "updated_at": session.updated_at.isoformat() if session.updated_at else "",
    }


def _operation_to_dict(operation: CollabOperation) -> dict[str, Any]:
    return {
        "operation_id": operation.id,
        "session_id": operation.session_id,
        "tenant_id": operation.tenant_id,
        "version": operation.version,
        "operation": operation.operation,
        "author": operation.author or "",
        "created_at": operation.created_at.isoformat() if operation.created_at else "",
    }


class CollaborationStore:
    """PostgreSQL-backed collaboration store with in-memory fallback semantics in tests."""

    def __init__(self, db_session_factory: Any | None = None) -> None:
        self._db = db_session_factory
        self._sessions: dict[tuple[str, str], dict[str, Any]] = {}
        self._operations: dict[tuple[str, str], list[dict[str, Any]]] = {}

    async def list_sessions(self, *, tenant_ctx: TenantContext) -> list[dict[str, Any]]:
        if self._db is None:
            return [s for (tid, _), s in self._sessions.items() if tid == tenant_ctx.tenant_id]
        async with self._db() as session, session.begin():  # noqa: SIM117
            async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                result = await session.execute(
                    select(CollabSession).where(CollabSession.tenant_id == tenant_ctx.tenant_id)
                )
                rows = result.scalars().all()
        return [_session_to_dict(row) for row in rows]

    async def create_session(
        self,
        *,
        tenant_ctx: TenantContext,
        name: str,
        mode: str,
        participants: list[str],
        goal_id: str | None = None,
        agent_id: str | None = None,
        content: str = "",
    ) -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        created_at = _now_iso()
        metadata = {"participants": participants, "goal_id": goal_id, "agent_id": agent_id}
        if self._db is None:
            record = {
                "session_id": session_id,
                "tenant_id": tenant_ctx.tenant_id,
                "name": name,
                "mode": mode,
                "status": "active",
                "content": content,
                "goal_id": goal_id,
                "agent_id": agent_id,
                "participants": participants,
                "participant_count": len(participants),
                "created_at": created_at,
                "updated_at": created_at,
            }
            self._sessions[(tenant_ctx.tenant_id, session_id)] = record
            self._operations[(tenant_ctx.tenant_id, session_id)] = []
            return dict(record)

        row = CollabSession(
            id=session_id,
            tenant_id=tenant_ctx.tenant_id,
            name=name,
            mode=mode,
            status="active",
            content=content,
            metadata_json=metadata,
        )
        async with self._db() as session, session.begin():  # noqa: SIM117
            async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                session.add(row)
        return _session_to_dict(row, participants=participants)

    async def get_session(
        self, *, tenant_ctx: TenantContext, session_id: str
    ) -> dict[str, Any] | None:
        if self._db is None:
            session = self._sessions.get((tenant_ctx.tenant_id, session_id))
            return dict(session) if session else None
        async with self._db() as session, session.begin():  # noqa: SIM117
            async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                result = await session.execute(
                    select(CollabSession).where(
                        CollabSession.id == session_id,
                        CollabSession.tenant_id == tenant_ctx.tenant_id,
                    )
                )
                row = result.scalar_one_or_none()
        return _session_to_dict(row) if row else None

    async def close_session(
        self, *, tenant_ctx: TenantContext, session_id: str
    ) -> dict[str, Any] | None:
        if self._db is None:
            session = self._sessions.get((tenant_ctx.tenant_id, session_id))
            if session is None:
                return None
            session["status"] = "closed"
            session["updated_at"] = _now_iso()
            return dict(session)
        async with self._db() as db_session, db_session.begin():  # noqa: SIM117
            async with sqlalchemy_rls_context(db_session, tenant_ctx.tenant_id):
                result = await db_session.execute(
                    select(CollabSession).where(
                        CollabSession.id == session_id,
                        CollabSession.tenant_id == tenant_ctx.tenant_id,
                    )
                )
                row = result.scalar_one_or_none()
                if row is None:
                    return None
                row.status = "closed"
                row.updated_at = datetime.now(UTC)
        return _session_to_dict(row)

    async def append_operation(
        self,
        *,
        tenant_ctx: TenantContext,
        session_id: str,
        operation: dict[str, Any],
        author: str,
    ) -> dict[str, Any]:
        if self._db is None:
            key = (tenant_ctx.tenant_id, session_id)
            if key not in self._sessions:
                raise KeyError(session_id)
            ops = self._operations.setdefault(key, [])
            record = {
                "operation_id": uuid.uuid4().hex,
                "session_id": session_id,
                "tenant_id": tenant_ctx.tenant_id,
                "version": len(ops) + 1,
                "operation": operation,
                "author": author,
                "created_at": _now_iso(),
            }
            ops.append(record)
            if operation.get("type") == "content_update":
                self._sessions[key]["content"] = str(operation.get("content", ""))
            return dict(record)

        async with self._db() as db_session, db_session.begin():  # noqa: SIM117
            async with sqlalchemy_rls_context(db_session, tenant_ctx.tenant_id):
                session_result = await db_session.execute(
                    select(CollabSession).where(
                        CollabSession.id == session_id,
                        CollabSession.tenant_id == tenant_ctx.tenant_id,
                    ).with_for_update()
                )
                session_row = session_result.scalar_one_or_none()
                if session_row is None:
                    raise KeyError(session_id)
                ops_result = await db_session.execute(
                    select(CollabOperation).where(
                        CollabOperation.session_id == session_id,
                        CollabOperation.tenant_id == tenant_ctx.tenant_id,
                    )
                )
                version = len(ops_result.scalars().all()) + 1
                row = CollabOperation(
                    id=uuid.uuid4().hex,
                    session_id=session_id,
                    tenant_id=tenant_ctx.tenant_id,
                    version=version,
                    operation=operation,
                    author=author,
                )
                db_session.add(row)
                if operation.get("type") == "content_update":
                    session_row.content = str(operation.get("content", ""))
                    session_row.updated_at = datetime.now(UTC)
        return _operation_to_dict(row)

    async def list_operations(
        self, *, tenant_ctx: TenantContext, session_id: str
    ) -> list[dict[str, Any]]:
        if self._db is None:
            return list(self._operations.get((tenant_ctx.tenant_id, session_id), []))
        async with self._db() as session, session.begin():  # noqa: SIM117
            async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                result = await session.execute(
                    select(CollabOperation)
                    .where(
                        CollabOperation.session_id == session_id,
                        CollabOperation.tenant_id == tenant_ctx.tenant_id,
                    )
                    .order_by(CollabOperation.version)
                )
                rows = result.scalars().all()
        return [_operation_to_dict(row) for row in rows]
