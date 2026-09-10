"""PostgresWorkflowApprovalStore — durable, cross-process store for workflow HITL.

The :class:`~app.workflow.hitl_extension.HITLWorkflowGateway` historically kept
pending approvals in an in-memory per-process dict (with an optional best-effort
Redis mirror). That made the workflow-HITL gate *process-local*: a run suspended
in an out-of-process Celery worker created its approval in the worker's memory,
invisible to the API's ``/approvals`` endpoints, and an API-side decision never
reached the worker.

This store persists the full :class:`WorkflowHITLRequest` as a JSONB document in
the ``workflow_approvals`` table (migration 0119), tenant-scoped by Row-Level
Security. A worker-created pending approval is therefore visible to the API's
listing/decision endpoints, and a decision written by the API is visible to the
worker — the two halves of cross-process workflow HITL (gap #2).

Every query sets the ``app.tenant_id`` Postgres GUC via ``SET LOCAL`` so RLS
enforces tenant isolation at the database layer (see ``app/db/rls.py``).
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from app.observability.logging import get_logger

if TYPE_CHECKING:
    from app.workflow.hitl_extension import WorkflowHITLRequest

_log = get_logger(__name__)

_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@runtime_checkable
class WorkflowApprovalStore(Protocol):
    """Persistence surface for workflow HITL approvals. See the Postgres impl."""

    async def save(self, req: WorkflowHITLRequest) -> None: ...

    async def get(
        self, request_id: str, tenant_id: str | None = None
    ) -> WorkflowHITLRequest | None: ...

    async def list_pending(
        self,
        tenant_id: str,
        assigned_to: str | None = None,
        priority: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[WorkflowHITLRequest], int]: ...

    async def get_stats(self, tenant_id: str) -> dict[str, Any]: ...


class PostgresWorkflowApprovalStore:
    """Async SQLAlchemy-backed approval store with per-query RLS enforcement."""

    def __init__(self, db_factory: Any) -> None:
        """Args:
        db_factory: async SQLAlchemy ``async_sessionmaker``
            (``app.state.db_session_factory``).
        """
        self._db = db_factory

    # ── RLS helper ────────────────────────────────────────────────────────────
    @staticmethod
    async def _set_tenant(session: Any, tenant_id: str) -> None:
        from sqlalchemy import text as sa_text

        await session.execute(
            sa_text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
        )

    # ── (De)serialization ─────────────────────────────────────────────────────
    @staticmethod
    def _to_payload(req: WorkflowHITLRequest) -> dict[str, Any]:
        return dataclasses.asdict(req)

    @staticmethod
    def _from_payload(payload: Any) -> WorkflowHITLRequest:
        from app.workflow.hitl_extension import WorkflowHITLRequest

        data = payload
        if isinstance(data, str):
            data = json.loads(data)
        # Guard against stray columns / schema drift: only feed known fields.
        fields = {f.name for f in dataclasses.fields(WorkflowHITLRequest)}
        return WorkflowHITLRequest(**{k: v for k, v in data.items() if k in fields})

    # ── Write ─────────────────────────────────────────────────────────────────
    async def save(self, req: WorkflowHITLRequest) -> None:
        """Upsert an approval. ``request_id`` is the primary key."""
        from sqlalchemy import text as sa_text

        payload = self._to_payload(req)
        async with self._db() as session:
            await self._set_tenant(session, req.tenant_id)
            await session.execute(
                sa_text(
                    "INSERT INTO workflow_approvals "
                    "(request_id, tenant_id, run_id, workflow_id, step_id, status, "
                    " priority, assigned_to, payload, created_at, updated_at) "
                    "VALUES (:request_id, CAST(:tenant_id AS uuid), :run_id, :workflow_id, "
                    " :step_id, :status, :priority, :assigned_to, CAST(:payload AS jsonb), "
                    " NOW(), NOW()) "
                    "ON CONFLICT (request_id) DO UPDATE SET "
                    " status = EXCLUDED.status, priority = EXCLUDED.priority, "
                    " assigned_to = EXCLUDED.assigned_to, payload = EXCLUDED.payload, "
                    " updated_at = NOW()"
                ),
                {
                    "request_id": req.request_id,
                    "tenant_id": req.tenant_id,
                    "run_id": req.run_id,
                    "workflow_id": req.workflow_id or None,
                    "step_id": req.step_id,
                    "status": req.status,
                    "priority": req.priority,
                    "assigned_to": req.assigned_to,
                    "payload": json.dumps(payload),
                },
            )
            await session.commit()

    # ── Read ──────────────────────────────────────────────────────────────────
    async def get(
        self, request_id: str, tenant_id: str | None = None
    ) -> WorkflowHITLRequest | None:
        """Load one approval by its opaque id.

        ``tenant_id`` must be supplied so RLS can authorize the read; without it
        (the gateway's legacy ``get_request(request_id)`` signature) there is no
        tenant context and RLS returns nothing, so this yields ``None`` and the
        caller falls back to its in-memory store.
        """
        if not tenant_id:
            return None
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "SELECT payload FROM workflow_approvals "
                        "WHERE request_id = :rid"
                    ),
                    {"rid": request_id},
                )
            ).first()
            if row is None:
                return None
            return self._from_payload(row[0])

    async def list_pending(
        self,
        tenant_id: str,
        assigned_to: str | None = None,
        priority: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[WorkflowHITLRequest], int]:
        from sqlalchemy import text as sa_text

        clauses = ["status = 'pending'"]
        params: dict[str, Any] = {}
        if assigned_to is not None:
            clauses.append("assigned_to = :assigned_to")
            params["assigned_to"] = assigned_to
        if priority is not None:
            clauses.append("priority = :priority")
            params["priority"] = priority
        where = " AND ".join(clauses)

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            rows = (
                await session.execute(
                    sa_text(f"SELECT payload FROM workflow_approvals WHERE {where}"),
                    params,
                )
            ).all()
        items = [self._from_payload(r[0]) for r in rows]
        items.sort(key=lambda r: (_PRIORITY_ORDER.get(r.priority, 99), r.created_at))
        total = len(items)
        start = (page - 1) * per_page
        return items[start : start + per_page], total

    async def get_stats(self, tenant_id: str) -> dict[str, Any]:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            rows = (
                await session.execute(
                    sa_text(
                        "SELECT status, payload FROM workflow_approvals"
                    )
                )
            ).all()

        pending = 0
        durations: list[float] = []
        for status, payload in rows:
            if status == "pending":
                pending += 1
            req = self._from_payload(payload)
            if req.reviewed_at:
                try:
                    created = datetime.fromisoformat(req.created_at)
                    reviewed = datetime.fromisoformat(req.reviewed_at)
                    durations.append((reviewed - created).total_seconds())
                except (ValueError, TypeError):
                    pass
        avg_resolution = sum(durations) / len(durations) if durations else 0.0
        return {
            "pending_count": pending,
            "total_requests": len(rows),
            "avg_resolution_seconds": avg_resolution,
        }

    async def list_by_run(
        self, tenant_id: str, run_id: str
    ) -> list[WorkflowHITLRequest]:
        """All approvals for a given run (used by cross-process resume/tests)."""
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            rows = (
                await session.execute(
                    sa_text(
                        "SELECT payload FROM workflow_approvals WHERE run_id = :run_id"
                    ),
                    {"run_id": run_id},
                )
            ).all()
        return [self._from_payload(r[0]) for r in rows]
