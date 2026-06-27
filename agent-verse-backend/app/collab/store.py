"""Tenant-scoped collaboration session store."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.db.models.intelligence import CollabOperation, CollabSession
from app.db.rls import sqlalchemy_rls_context
from app.tenancy.context import TenantContext


class VersionConflictError(Exception):
    """Raised when an optimistic concurrency check fails."""

    def __init__(
        self, message: str, current_version: int = 0, expected_version: int = 0
    ) -> None:
        super().__init__(message)
        self.current_version = current_version
        self.expected_version = expected_version


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
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        if self._db is None:
            key = (tenant_ctx.tenant_id, session_id)
            if key not in self._sessions:
                raise KeyError(session_id)
            ops = self._operations.setdefault(key, [])

            # Optimistic check for in-memory fallback
            current_version = len(ops)
            if expected_version is not None and current_version != expected_version:
                raise VersionConflictError(
                    f"Version conflict: expected {expected_version}, got {current_version}",
                    current_version=current_version,
                    expected_version=expected_version,
                )

            record = {
                "operation_id": uuid.uuid4().hex,
                "session_id": session_id,
                "tenant_id": tenant_ctx.tenant_id,
                "version": current_version + 1,
                "operation": operation,
                "author": author,
                "created_at": _now_iso(),
            }
            ops.append(record)
            if operation.get("type") == "content_update":
                self._sessions[key]["content"] = str(operation.get("content", ""))
            return dict(record)

        # PostgreSQL path: optimistic concurrency with INSERT ... SELECT
        import json as _json

        from sqlalchemy import text

        async with self._db() as db_session, db_session.begin():
            async with sqlalchemy_rls_context(db_session, tenant_ctx.tenant_id):
                # 1. Verify session exists (no lock needed)
                session_check = await db_session.execute(
                    select(CollabSession.id, CollabSession.content, CollabSession.updated_at).where(
                        CollabSession.id == session_id,
                        CollabSession.tenant_id == tenant_ctx.tenant_id,
                    )
                )
                session_row = session_check.first()
                if session_row is None:
                    raise KeyError(session_id)

                # 2. Atomic INSERT ... SELECT with HAVING-based version check.
                #    Avoids SELECT FOR UPDATE and O(N) count.
                insert_result = await db_session.execute(
                    text("""
                        INSERT INTO collab_operations
                            (id, session_id, tenant_id, version, operation, author, created_at)
                        SELECT
                            :op_id,
                            :session_id,
                            :tenant_id,
                            COALESCE(MAX(version), 0) + 1,
                            :operation::jsonb,
                            :author,
                            NOW()
                        FROM collab_operations
                        WHERE session_id = :session_id2 AND tenant_id = :tenant_id2
                        HAVING :expected_version IS NULL
                            OR COALESCE(MAX(version), 0) = :expected_version
                        RETURNING id, version, created_at
                    """),
                    {
                        "op_id": uuid.uuid4().hex,
                        "session_id": session_id,
                        "tenant_id": tenant_ctx.tenant_id,
                        "session_id2": session_id,
                        "tenant_id2": tenant_ctx.tenant_id,
                        "operation": _json.dumps(operation),
                        "author": author,
                        "expected_version": expected_version,
                    },
                )

                inserted = insert_result.fetchone()
                if inserted is None:
                    # HAVING clause rejected — version conflict; fetch actual version
                    max_result = await db_session.execute(
                        text(
                            "SELECT COALESCE(MAX(version), 0) FROM collab_operations "
                            "WHERE session_id = :sid AND tenant_id = :tid"
                        ),
                        {"sid": session_id, "tid": tenant_ctx.tenant_id},
                    )
                    current_v = max_result.scalar() or 0
                    raise VersionConflictError(
                        f"Optimistic concurrency conflict: expected {expected_version}, current {current_v}",
                        current_version=current_v,
                        expected_version=expected_version,
                    )

                op_id, new_version, created_at = inserted

                # 3. Update session content (last writer wins)
                if operation.get("type") == "content_update":
                    await db_session.execute(
                        text(
                            "UPDATE collab_sessions SET content = :content, updated_at = NOW() "
                            "WHERE id = :sid AND tenant_id = :tid"
                        ),
                        {
                            "content": str(operation.get("content", "")),
                            "sid": session_id,
                            "tid": tenant_ctx.tenant_id,
                        },
                    )

        return {
            "operation_id": op_id,
            "session_id": session_id,
            "tenant_id": tenant_ctx.tenant_id,
            "version": new_version,
            "operation": operation,
            "author": author,
            "created_at": (
                created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
            ),
        }

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
