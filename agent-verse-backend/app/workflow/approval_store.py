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
            # Explicit tenant guard on the upsert as well as RLS: on a BYPASSRLS
            # connection a colliding request_id must never rewrite another
            # tenant's approval. No row back = that collision -> refuse.
            row = (
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
                        " updated_at = NOW() "
                        "WHERE workflow_approvals.tenant_id = EXCLUDED.tenant_id "
                        "RETURNING request_id"
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
            ).first()
            await session.commit()
        if row is None:
            raise PermissionError(
                f"approval {req.request_id!r} belongs to another tenant; not overwritten"
            )

    async def decide_if_pending(self, req: WorkflowHITLRequest) -> bool:
        """Record ``req``'s decision only if the approval is still pending AND
        its run has not reached a terminal status.

        One conditional UPDATE, so of two reviewers (on any replicas) deciding
        the same approval exactly one gets ``True`` — the other must not resume
        the run. Old bug: read-then-upsert let both win (last write wins) and
        both resume callbacks dispatched the run.
        """
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, req.tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "UPDATE workflow_approvals SET status = :status, "
                        " payload = CAST(:payload AS jsonb), updated_at = NOW() "
                        "WHERE request_id = :rid AND status = 'pending' "
                        "AND tenant_id = CAST(:tid AS uuid) "
                        # The run must still be live: a decision on a gate of a
                        # cancelled / finished run must not resume it.
                        "AND EXISTS (SELECT 1 FROM workflow_runs r "
                        "  WHERE r.id::text = workflow_approvals.run_id "
                        "  AND r.tenant_id = CAST(:tid AS uuid) "
                        "  AND r.status NOT IN ('complete', 'failed', 'cancelled', 'timed_out')) "
                        "RETURNING request_id"
                    ),
                    {
                        "tid": req.tenant_id,
                        "status": req.status,
                        "payload": json.dumps(self._to_payload(req)),
                        "rid": req.request_id,
                    },
                )
            ).first()
            await session.commit()
        if row is not None:
            # A12: the tenant's agent_generated Sources index the decision now.
            from app.ingestion.agent_generated_events import notify_agent_generated

            await notify_agent_generated(
                req.tenant_id, "hitl_decision", req.request_id, db_factory=self._db
            )
        return row is not None

    async def mutate_if_pending(
        self,
        request_id: str,
        tenant_id: str,
        *,
        discussion_entry: dict[str, Any],
        assignment: dict[str, Any] | None = None,
    ) -> WorkflowHITLRequest | None:
        """Append a discussion entry (and optionally reassign) a PENDING approval.

        WF-41: delegate / escalate / comment used to upsert a stale full copy
        of the approval — status included — so racing a decision flipped the
        decided approval back to ``pending`` and allowed a second decision and a
        second resume. This is one conditional UPDATE that never writes
        ``status``; ``None`` means the approval is not pending (or not found).
        """
        from sqlalchemy import text as sa_text

        patch = dict(assignment or {})
        sets = [
            "payload = jsonb_set(payload || CAST(:patch AS jsonb), '{discussion}', "
            " COALESCE(payload->'discussion', '[]'::jsonb) || "
            " jsonb_build_array(CAST(:entry AS jsonb)))",
            "updated_at = NOW()",
        ]
        if "assigned_to" in patch:
            sets.append("assigned_to = :assigned_to")
        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        f"UPDATE workflow_approvals SET {', '.join(sets)} "
                        "WHERE request_id = :rid AND tenant_id = CAST(:tid AS uuid) "
                        "AND status = 'pending' RETURNING payload"
                    ),
                    {
                        "rid": request_id,
                        "tid": tenant_id,
                        "patch": json.dumps(patch),
                        "entry": json.dumps(discussion_entry, default=str),
                        "assigned_to": patch.get("assigned_to"),
                    },
                )
            ).first()
            await session.commit()
        return self._from_payload(row[0]) if row is not None else None

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
                        "WHERE request_id = :rid AND tenant_id = CAST(:tid AS uuid)"
                    ),
                    {"rid": request_id, "tid": tenant_id},
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

        # Explicit tenant predicate as well as RLS (BYPASSRLS connections).
        clauses = ["tenant_id = CAST(:tid AS uuid)", "status = 'pending'"]
        params: dict[str, Any] = {"tid": tenant_id}
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

    async def list_pending_all_tenants(
        self, *, limit: int = 500, system_db: Any = None
    ) -> list[WorkflowHITLRequest]:
        """Cross-tenant pending approvals for the SLA sweep (maintenance role).

        ``workflow_approvals`` is RLS-scoped, so this runs under
        :func:`app.db.rls.system_session` on the BYPASSRLS maintenance factory.
        """
        from sqlalchemy import text as sa_text

        from app.db.rls import system_session
        from app.db.session import get_system_session_factory

        factory = system_db or get_system_session_factory()
        async with factory() as session, session.begin(), system_session(session):
            rows = (
                await session.execute(
                    sa_text(
                        "SELECT payload FROM workflow_approvals WHERE status = 'pending' "
                        "ORDER BY created_at LIMIT :lim"
                    ),
                    {"lim": limit},
                )
            ).all()
        return [self._from_payload(r[0]) for r in rows]

    async def get_stats(self, tenant_id: str) -> dict[str, Any]:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            rows = (
                await session.execute(
                    sa_text(
                        "SELECT status, payload FROM workflow_approvals "
                        "WHERE tenant_id = CAST(:tid AS uuid)"
                    ),
                    {"tid": tenant_id},
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

    # ── Reviewer directory (auto-assignment) ──────────────────────────────────
    async def role_members(self, tenant_id: str, role: str) -> list[str]:
        """Principals holding ``role`` in the tenant (``user_roles``), sorted."""
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            rows = (
                await session.execute(
                    sa_text(
                        "SELECT DISTINCT user_id FROM user_roles "
                        "WHERE tenant_id = :tid AND role = :role ORDER BY user_id"
                    ),
                    {"tid": tenant_id, "role": role},
                )
            ).all()
        return [str(r[0]) for r in rows]

    async def assignee_load(
        self, tenant_id: str, users: list[str]
    ) -> dict[str, tuple[int, datetime | None]]:
        """Per user: (pending approvals assigned, when they were last assigned one).

        Read from the shared table, so every replica sees the same numbers."""
        if not users:
            return {}
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            rows = (
                await session.execute(
                    sa_text(
                        "SELECT assigned_to, "
                        " COUNT(*) FILTER (WHERE status = 'pending'), MAX(created_at) "
                        "FROM workflow_approvals WHERE assigned_to = ANY(:users) "
                        "AND tenant_id = CAST(:tid AS uuid) "
                        "GROUP BY assigned_to"
                    ),
                    {"users": list(users), "tid": tenant_id},
                )
            ).all()
        return {str(r[0]): (int(r[1] or 0), r[2]) for r in rows}

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
                        "SELECT payload FROM workflow_approvals WHERE run_id = :run_id "
                        "AND tenant_id = CAST(:tid AS uuid)"
                    ),
                    {"run_id": run_id, "tid": tenant_id},
                )
            ).all()
        return [self._from_payload(r[0]) for r in rows]
