"""WorkflowRunStore — persistence for workflow runs and step results.

Two implementations:
  * ``WorkflowRunStore`` — a ``Protocol`` describing the surface every caller
    (``WorkflowRunner``, ``WorkflowService``, the Celery maintenance tasks and the
    compiler step-result hook) relies on.
  * ``PostgresWorkflowRunStore`` — async SQLAlchemy implementation backed by the
    ``workflow_runs`` / ``workflow_step_results`` / ``workflow_definitions`` tables
    (migration 0108). Every query sets the ``app.tenant_id`` Postgres GUC so
    Row-Level Security enforces tenant isolation at the database layer.

The store is wired in the FastAPI lifespan:

    from app.workflow.run_store import PostgresWorkflowRunStore
    run_store = PostgresWorkflowRunStore(db_factory)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.observability.logging import get_logger

_log = get_logger(__name__)

# Statuses that mean the run is finished — used to stamp ``completed_at``.
_TERMINAL_STATUSES = {"complete", "failed", "cancelled", "timed_out"}


def _as_str(value: Any) -> str:
    """Coerce an enum / str status into its plain string value."""
    return getattr(value, "value", value)


def _as_obj(value: Any) -> Any:
    """JSONB comes back as a dict from the asyncpg codec, but may arrive as a
    JSON string depending on the driver/codec. Normalise to a Python object."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _duration_ms(started: Any, finished: Any) -> float | None:
    if isinstance(started, datetime) and isinstance(finished, datetime):
        return (finished - started).total_seconds() * 1000.0
    return None


@runtime_checkable
class WorkflowRunStore(Protocol):
    """Persistence surface for workflow runs. See ``PostgresWorkflowRunStore``."""

    async def create(
        self,
        *,
        run_id: str,
        workflow_id: str,
        tenant_id: str,
        trigger_type: str = "api",
        trigger_payload: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        labels: dict[str, str] | None = None,
        is_test_run: bool = False,
    ) -> None: ...

    async def get(self, tenant_id: str, run_id: str) -> dict[str, Any] | None: ...

    async def list(
        self,
        tenant_id: str,
        *,
        workflow_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]: ...

    async def update_status(
        self,
        run_id: str,
        status: Any,
        *,
        tenant_id: str,
        error: str | None = None,
        error_step_id: str | None = None,
        outputs: dict[str, Any] | None = None,
        current_step_id: str | None = None,
        cost_usd: float | None = None,
        tokens_used: int | None = None,
    ) -> bool: ...

    async def get_workflow_id(self, run_id: str, tenant_id: str | None = None) -> str: ...

    async def get_definition(self, workflow_id: str, tenant_id: str) -> dict[str, Any]: ...

    async def record_step_start(
        self,
        *,
        run_id: str,
        tenant_id: str,
        step_id: str,
        step_type: str,
        step_name: str | None = None,
        resolved_input: dict[str, Any] | None = None,
        attempt_number: int = 1,
    ) -> str: ...

    async def record_step_finish(
        self,
        *,
        run_id: str,
        tenant_id: str,
        step_id: str,
        status: Any,
        output: dict[str, Any] | None = None,
        error: str | None = None,
        cost_usd: float | None = None,
    ) -> bool: ...

    async def list_step_results(
        self, tenant_id: str, run_id: str
    ) -> list[dict[str, Any]]: ...

    async def get_step_result(
        self, tenant_id: str, run_id: str, step_id: str
    ) -> dict[str, Any] | None: ...

    async def get_retryable_webhooks(self, max_attempts: int = 3) -> list[dict[str, Any]]: ...

    async def delete_expired_runs(self) -> int: ...

    # ── Advanced features (2.W-2) ─────────────────────────────────────────────
    async def list_versions(
        self, tenant_id: str, workflow_id: str
    ) -> list[dict[str, Any]]: ...

    async def get_definition_version(
        self, tenant_id: str, workflow_id: str, version: str
    ) -> dict[str, Any] | None: ...

    async def get_permissions(
        self, tenant_id: str, workflow_id: str
    ) -> list[dict[str, Any]]: ...

    async def add_permission(
        self,
        tenant_id: str,
        workflow_id: str,
        *,
        subject_type: str,
        subject_id: str,
        permission: str,
    ) -> dict[str, Any]: ...

    async def remove_permission(
        self, tenant_id: str, workflow_id: str, permission_id: str
    ) -> bool: ...

    async def workflow_run_stats(
        self, tenant_id: str, workflow_id: str, days: int = 30
    ) -> dict[str, Any]: ...

    async def aggregate_run_stats(
        self, tenant_id: str, days: int = 30
    ) -> dict[str, Any]: ...

    async def list_webhook_events(
        self, tenant_id: str, workflow_id: str, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]: ...


class PostgresWorkflowRunStore:
    """Async SQLAlchemy-backed run store with per-query RLS enforcement."""

    def __init__(self, db_factory: Any) -> None:
        """Args:
        db_factory: async SQLAlchemy ``async_sessionmaker`` (``app.state.db_session_factory``).
        """
        self._db = db_factory

    # ── RLS helper ────────────────────────────────────────────────────────────
    @staticmethod
    async def _set_tenant(session: Any, tenant_id: str) -> None:
        from sqlalchemy import text as sa_text

        await session.execute(
            sa_text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
        )

    # ── Runs ──────────────────────────────────────────────────────────────────
    async def create(
        self,
        *,
        run_id: str,
        workflow_id: str,
        tenant_id: str,
        trigger_type: str = "api",
        trigger_payload: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        labels: dict[str, str] | None = None,
        is_test_run: bool = False,
    ) -> None:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            await session.execute(
                sa_text(
                    "INSERT INTO workflow_runs "
                    "(id, tenant_id, workflow_id, trigger_type, trigger_payload, inputs, "
                    " status, labels, is_test_run, created_at) "
                    "VALUES (:id, CAST(:tenant_id AS uuid), CAST(:workflow_id AS uuid), "
                    " :trigger_type, CAST(:trigger_payload AS jsonb), CAST(:inputs AS jsonb), "
                    " 'pending', CAST(:labels AS jsonb), :is_test_run, NOW())"
                ),
                {
                    "id": run_id,
                    "tenant_id": tenant_id,
                    "workflow_id": workflow_id,
                    "trigger_type": trigger_type,
                    "trigger_payload": json.dumps(trigger_payload) if trigger_payload else None,
                    "inputs": json.dumps(inputs or {}),
                    "labels": json.dumps(labels or {}),
                    "is_test_run": is_test_run,
                },
            )
            await session.commit()

    async def get(self, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "SELECT r.*, d.name AS workflow_name, "
                        " (SELECT COUNT(*) FROM workflow_step_results s WHERE s.run_id = r.id) "
                        "   AS step_count "
                        "FROM workflow_runs r "
                        "LEFT JOIN workflow_definitions d ON d.id = r.workflow_id "
                        "WHERE r.id = CAST(:rid AS uuid)"
                    ),
                    {"rid": run_id},
                )
            ).mappings().first()
            return self._row_to_run(row) if row else None

    async def list(
        self,
        tenant_id: str,
        *,
        workflow_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        from sqlalchemy import text as sa_text

        clauses = ["TRUE"]
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if workflow_id:
            clauses.append("r.workflow_id = CAST(:workflow_id AS uuid)")
            params["workflow_id"] = workflow_id
        if status:
            clauses.append("r.status = :status")
            params["status"] = status
        where = " AND ".join(clauses)

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            total = (
                await session.execute(
                    sa_text(f"SELECT COUNT(*) FROM workflow_runs r WHERE {where}"), params
                )
            ).scalar_one()
            rows = (
                await session.execute(
                    sa_text(
                        "SELECT r.*, d.name AS workflow_name, "
                        " (SELECT COUNT(*) FROM workflow_step_results s WHERE s.run_id = r.id) "
                        "   AS step_count "
                        "FROM workflow_runs r "
                        "LEFT JOIN workflow_definitions d ON d.id = r.workflow_id "
                        f"WHERE {where} "
                        "ORDER BY r.created_at DESC LIMIT :limit OFFSET :offset"
                    ),
                    params,
                )
            ).mappings().all()
            return [self._row_to_run(r) for r in rows], int(total)

    async def update_status(
        self,
        run_id: str,
        status: Any,
        *,
        tenant_id: str,
        error: str | None = None,
        error_step_id: str | None = None,
        outputs: dict[str, Any] | None = None,
        current_step_id: str | None = None,
        cost_usd: float | None = None,
        tokens_used: int | None = None,
    ) -> bool:
        from sqlalchemy import text as sa_text

        status_str = _as_str(status)
        sets = ["status = :status"]
        params: dict[str, Any] = {"status": status_str, "rid": run_id}
        # Stamp started_at the first time a run leaves 'pending'.
        if status_str == "running":
            sets.append("started_at = COALESCE(started_at, NOW())")
        if status_str in _TERMINAL_STATUSES:
            sets.append("completed_at = NOW()")
        if error is not None:
            sets.append("error = :error")
            params["error"] = error
        if error_step_id is not None:
            sets.append("error_step_id = :error_step_id")
            params["error_step_id"] = error_step_id
        if outputs is not None:
            sets.append("outputs = CAST(:outputs AS jsonb)")
            params["outputs"] = json.dumps(outputs)
        if current_step_id is not None:
            sets.append("current_step_id = :current_step_id")
            params["current_step_id"] = current_step_id
        if cost_usd is not None:
            sets.append("cost_usd = :cost_usd")
            params["cost_usd"] = cost_usd
        if tokens_used is not None:
            sets.append("tokens_used = :tokens_used")
            params["tokens_used"] = tokens_used

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            result = await session.execute(
                sa_text(
                    f"UPDATE workflow_runs SET {', '.join(sets)} "
                    "WHERE id = CAST(:rid AS uuid)"
                ),
                params,
            )
            await session.commit()
            return bool(result.rowcount)

    async def get_workflow_id(self, run_id: str, tenant_id: str | None = None) -> str:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            if tenant_id is not None:
                await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "SELECT workflow_id FROM workflow_runs WHERE id = CAST(:rid AS uuid)"
                    ),
                    {"rid": run_id},
                )
            ).first()
            return str(row[0]) if row and row[0] is not None else ""

    async def get_status(self, tenant_id: str, run_id: str) -> str | None:
        """Lightweight current-status read — used by the engine's cooperative
        cancel/pause check between steps (avoids the heavier get() per step)."""
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text("SELECT status FROM workflow_runs WHERE id = CAST(:rid AS uuid)"),
                    {"rid": run_id},
                )
            ).first()
            return str(row[0]) if row and row[0] is not None else None

    async def get_step_result(
        self, tenant_id: str, run_id: str, step_id: str
    ) -> dict[str, Any] | None:
        """Return the latest persisted result row for one step of a run, or None.

        Lets a resumed run skip steps already completed in a prior (paused)
        attempt instead of redoing their work."""
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "SELECT * FROM workflow_step_results "
                        "WHERE run_id = CAST(:rid AS uuid) AND step_id = :sid "
                        "ORDER BY attempt_number DESC, started_at DESC LIMIT 1"
                    ),
                    {"rid": run_id, "sid": step_id},
                )
            ).mappings().first()
            return self._row_to_step(row) if row else None

    async def get_definition(self, workflow_id: str, tenant_id: str) -> dict[str, Any]:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            # The run engine's schema (migration 0108) is built around the
            # ``workflow_definitions`` table (uuid id). The visual-builder create
            # path (POST /api/v1/workflows -> WorkflowService -> _WorkflowStore)
            # writes the DSL to the legacy ``workflows`` table (Text id) but now
            # also mirrors each workflow into ``workflow_definitions`` with the
            # same id (see _WorkflowStore._bridge_upsert_definition; migration
            # 0115 backfills pre-existing rows). So this read resolves the DSL for
            # any API-created workflow, and workflow_runs' FK is satisfied.
            row = (
                await session.execute(
                    sa_text(
                        "SELECT definition_json FROM workflow_definitions "
                        "WHERE id = CAST(:wid AS uuid)"
                    ),
                    {"wid": workflow_id},
                )
            ).first()
            if not row or row[0] is None:
                raise KeyError(f"workflow definition {workflow_id!r} not found")
            return _as_obj(row[0])

    # ── Step results ──────────────────────────────────────────────────────────
    async def record_step_start(
        self,
        *,
        run_id: str,
        tenant_id: str,
        step_id: str,
        step_type: str,
        step_name: str | None = None,
        resolved_input: dict[str, Any] | None = None,
        attempt_number: int = 1,
    ) -> str:
        from sqlalchemy import text as sa_text

        result_id = str(uuid.uuid4())
        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            await session.execute(
                sa_text(
                    "INSERT INTO workflow_step_results "
                    "(id, run_id, tenant_id, step_id, step_type, step_name, status, "
                    " resolved_input, attempt_number, started_at) "
                    "VALUES (:id, CAST(:run_id AS uuid), CAST(:tenant_id AS uuid), :step_id, "
                    " :step_type, :step_name, 'running', CAST(:resolved_input AS jsonb), "
                    " :attempt_number, NOW())"
                ),
                {
                    "id": result_id,
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "step_id": step_id,
                    "step_type": step_type,
                    "step_name": step_name,
                    "resolved_input": json.dumps(resolved_input) if resolved_input else None,
                    "attempt_number": attempt_number,
                },
            )
            await session.commit()
            return result_id

    async def record_step_finish(
        self,
        *,
        run_id: str,
        tenant_id: str,
        step_id: str,
        status: Any,
        output: dict[str, Any] | None = None,
        error: str | None = None,
        cost_usd: float | None = None,
    ) -> bool:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            # Target the most recent (highest attempt) started row for this step.
            result = await session.execute(
                sa_text(
                    "UPDATE workflow_step_results SET "
                    " status = :status, output = CAST(:output AS jsonb), error = :error, "
                    " cost_usd = COALESCE(:cost_usd, cost_usd), completed_at = NOW(), "
                    " duration_ms = CAST(EXTRACT(EPOCH FROM (NOW() - started_at)) * 1000 AS int) "
                    "WHERE id = ("
                    "  SELECT id FROM workflow_step_results "
                    "  WHERE run_id = CAST(:run_id AS uuid) AND step_id = :step_id "
                    "  ORDER BY attempt_number DESC, started_at DESC LIMIT 1)"
                ),
                {
                    "status": _as_str(status),
                    "output": json.dumps(output) if output is not None else None,
                    "error": error,
                    "cost_usd": cost_usd,
                    "run_id": run_id,
                    "step_id": step_id,
                },
            )
            await session.commit()
            return bool(result.rowcount)

    async def list_step_results(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            rows = (
                await session.execute(
                    sa_text(
                        "SELECT * FROM workflow_step_results "
                        "WHERE run_id = CAST(:rid AS uuid) "
                        "ORDER BY started_at ASC NULLS LAST, step_id ASC"
                    ),
                    {"rid": run_id},
                )
            ).mappings().all()
            return [self._row_to_step(r) for r in rows]

    async def get_step_result(
        self, tenant_id: str, run_id: str, step_id: str
    ) -> dict[str, Any] | None:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "SELECT * FROM workflow_step_results "
                        "WHERE run_id = CAST(:rid AS uuid) AND step_id = :step_id "
                        "ORDER BY attempt_number DESC, started_at DESC LIMIT 1"
                    ),
                    {"rid": run_id, "step_id": step_id},
                )
            ).mappings().first()
            return self._row_to_step(row) if row else None

    # ── Maintenance (cross-tenant) ────────────────────────────────────────────
    async def get_retryable_webhooks(self, max_attempts: int = 3) -> list[dict[str, Any]]:
        from sqlalchemy import text as sa_text

        from app.db.rls import system_session

        async with self._db() as session, session.begin(), system_session(session):
            rows = (
                await session.execute(
                    sa_text(
                        "SELECT id, tenant_id, workflow_id, payload FROM workflow_webhook_events "
                        "WHERE status IN ('pending', 'failed') AND attempts < :max_attempts "
                        "ORDER BY received_at ASC LIMIT 100"
                    ),
                    {"max_attempts": max_attempts},
                )
            ).mappings().all()
            return [
                {
                    "id": str(r["id"]),
                    "tenant_id": str(r["tenant_id"]),
                    "workflow_id": str(r["workflow_id"]) if r["workflow_id"] else None,
                    "payload": _as_obj(r["payload"]) or {},
                }
                for r in rows
            ]

    async def delete_expired_runs(self) -> int:
        from sqlalchemy import text as sa_text

        from app.db.rls import system_session

        async with self._db() as session, session.begin():
            async with system_session(session):
                result = await session.execute(
                    sa_text(
                        "DELETE FROM workflow_runs r USING workflow_definitions d "
                        "WHERE r.workflow_id = d.id "
                        "AND d.run_retention_days IS NOT NULL "
                        "AND r.created_at < NOW() - (d.run_retention_days || ' days')::interval"
                    )
                )
            return int(result.rowcount or 0)

    # ── Versions (workflow_definition_versions) ───────────────────────────────
    async def list_versions(self, tenant_id: str, workflow_id: str) -> list[dict[str, Any]]:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            rows = (
                await session.execute(
                    sa_text(
                        "SELECT id, version, change_summary, published_by, published_at "
                        "FROM workflow_definition_versions "
                        "WHERE workflow_id = CAST(:wid AS uuid) "
                        "ORDER BY published_at DESC"
                    ),
                    {"wid": workflow_id},
                )
            ).mappings().all()
            return [
                {
                    "version_id": str(r["id"]),
                    "version": r["version"],
                    "change_summary": r["change_summary"],
                    "published_by": str(r["published_by"]) if r["published_by"] else None,
                    "published_at": _iso(r["published_at"]),
                }
                for r in rows
            ]

    async def get_definition_version(
        self, tenant_id: str, workflow_id: str, version: str
    ) -> dict[str, Any] | None:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "SELECT version, definition_yaml, definition_json "
                        "FROM workflow_definition_versions "
                        "WHERE workflow_id = CAST(:wid AS uuid) AND version = :ver"
                    ),
                    {"wid": workflow_id, "ver": version},
                )
            ).mappings().first()
            if row is None:
                return None
            return {
                "version": row["version"],
                "definition_yaml": row["definition_yaml"],
                "definition_json": _as_obj(row["definition_json"]) or {},
            }

    # ── Permissions (workflow_permissions) ────────────────────────────────────
    async def get_permissions(self, tenant_id: str, workflow_id: str) -> list[dict[str, Any]]:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            rows = (
                await session.execute(
                    sa_text(
                        "SELECT id, subject_type, subject_id, permission, granted_by, granted_at "
                        "FROM workflow_permissions "
                        "WHERE workflow_id = CAST(:wid AS uuid) ORDER BY granted_at DESC"
                    ),
                    {"wid": workflow_id},
                )
            ).mappings().all()
            return [
                {
                    "id": str(r["id"]),
                    "subject_type": r["subject_type"],
                    "subject_id": r["subject_id"],
                    "permission": r["permission"],
                    "granted_by": str(r["granted_by"]) if r["granted_by"] else None,
                    "granted_at": _iso(r["granted_at"]),
                }
                for r in rows
            ]

    async def add_permission(
        self,
        tenant_id: str,
        workflow_id: str,
        *,
        subject_type: str,
        subject_id: str,
        permission: str,
    ) -> dict[str, Any]:
        from sqlalchemy import text as sa_text

        async with self._db() as session, session.begin():
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "INSERT INTO workflow_permissions "
                        "  (workflow_id, tenant_id, subject_type, subject_id, permission) "
                        "VALUES (CAST(:wid AS uuid), CAST(:tid AS uuid), :st, :sid, :perm) "
                        "ON CONFLICT (workflow_id, subject_type, subject_id, permission) "
                        "  DO UPDATE SET granted_at = NOW() "
                        "RETURNING id, subject_type, subject_id, permission, granted_at"
                    ),
                    {
                        "wid": workflow_id,
                        "tid": tenant_id,
                        "st": subject_type,
                        "sid": subject_id,
                        "perm": permission,
                    },
                )
            ).mappings().first()
            return {
                "id": str(row["id"]),
                "subject_type": row["subject_type"],
                "subject_id": row["subject_id"],
                "permission": row["permission"],
                "granted_at": _iso(row["granted_at"]),
            }

    async def remove_permission(
        self, tenant_id: str, workflow_id: str, permission_id: str
    ) -> bool:
        from sqlalchemy import text as sa_text

        async with self._db() as session, session.begin():
            await self._set_tenant(session, tenant_id)
            result = await session.execute(
                sa_text(
                    "DELETE FROM workflow_permissions "
                    "WHERE id = CAST(:pid AS uuid) AND workflow_id = CAST(:wid AS uuid)"
                ),
                {"pid": permission_id, "wid": workflow_id},
            )
            return int(result.rowcount or 0) > 0

    # ── Analytics (aggregated from workflow_runs) ─────────────────────────────
    _STATS_SELECT = (
        "SELECT COUNT(*) AS total, "
        " COUNT(*) FILTER (WHERE status = 'complete') AS completed, "
        " COUNT(*) FILTER (WHERE status IN ('failed', 'timed_out')) AS failed, "
        " COALESCE(AVG(EXTRACT(EPOCH FROM (completed_at - started_at))) "
        "   FILTER (WHERE completed_at IS NOT NULL AND started_at IS NOT NULL), 0) "
        "   AS avg_duration_s "
        "FROM workflow_runs "
    )

    @staticmethod
    def _stats_row(row: Any) -> dict[str, Any]:
        return {
            "total": int(row["total"] or 0),
            "completed": int(row["completed"] or 0),
            "failed": int(row["failed"] or 0),
            "avg_duration_s": float(row["avg_duration_s"] or 0.0),
        }

    async def workflow_run_stats(
        self, tenant_id: str, workflow_id: str, days: int = 30
    ) -> dict[str, Any]:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        self._STATS_SELECT
                        + "WHERE workflow_id = CAST(:wid AS uuid) "
                        + "AND created_at >= NOW() - make_interval(days => :days)"
                    ),
                    {"wid": workflow_id, "days": int(days)},
                )
            ).mappings().first()
            return self._stats_row(row)

    async def aggregate_run_stats(self, tenant_id: str, days: int = 30) -> dict[str, Any]:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        self._STATS_SELECT
                        + "WHERE created_at >= NOW() - make_interval(days => :days)"
                    ),
                    {"days": int(days)},
                )
            ).mappings().first()
            return self._stats_row(row)

    # ── Webhook events (workflow_webhook_events) ──────────────────────────────
    async def list_webhook_events(
        self, tenant_id: str, workflow_id: str, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            params = {"wid": workflow_id, "limit": limit, "offset": offset}
            total = (
                await session.execute(
                    sa_text(
                        "SELECT COUNT(*) FROM workflow_webhook_events "
                        "WHERE workflow_id = CAST(:wid AS uuid)"
                    ),
                    {"wid": workflow_id},
                )
            ).scalar_one()
            rows = (
                await session.execute(
                    sa_text(
                        "SELECT id, webhook_token, status, attempts, last_error, run_id, "
                        " received_at, last_attempted_at, completed_at "
                        "FROM workflow_webhook_events "
                        "WHERE workflow_id = CAST(:wid AS uuid) "
                        "ORDER BY received_at DESC LIMIT :limit OFFSET :offset"
                    ),
                    params,
                )
            ).mappings().all()
            events = [
                {
                    "id": str(r["id"]),
                    "webhook_token": r["webhook_token"],
                    "status": r["status"],
                    "attempts": int(r["attempts"] or 0),
                    "last_error": r["last_error"],
                    "run_id": str(r["run_id"]) if r["run_id"] else None,
                    "received_at": _iso(r["received_at"]),
                    "last_attempted_at": _iso(r["last_attempted_at"]),
                    "completed_at": _iso(r["completed_at"]),
                }
                for r in rows
            ]
            return events, int(total)

    # ── Row mappers ───────────────────────────────────────────────────────────
    @staticmethod
    def _row_to_run(row: Any) -> dict[str, Any]:
        return {
            "run_id": str(row["id"]),
            "workflow_id": str(row["workflow_id"]) if row["workflow_id"] else "",
            "workflow_name": row.get("workflow_name"),
            "status": row["status"],
            "inputs": _as_obj(row["inputs"]) or {},
            "outputs": _as_obj(row["outputs"]) or {},
            "error": row["error"],
            "started_at": _iso(row["started_at"]),
            "finished_at": _iso(row["completed_at"]),
            "duration_ms": _duration_ms(row["started_at"], row["completed_at"]),
            "step_count": int(row.get("step_count") or 0),
            "cost_usd": float(row["cost_usd"] or 0),
            "tokens_used": int(row.get("tokens_used") or 0),
        }

    @staticmethod
    def _row_to_step(row: Any) -> dict[str, Any]:
        return {
            "step_id": row["step_id"],
            "step_type": row["step_type"],
            "status": row["status"],
            "input": _as_obj(row["resolved_input"]),
            "output": _as_obj(row["output"]),
            "error": row["error"],
            "started_at": _iso(row["started_at"]),
            "finished_at": _iso(row["completed_at"]),
            "duration_ms": float(row["duration_ms"]) if row["duration_ms"] is not None else None,
        }
