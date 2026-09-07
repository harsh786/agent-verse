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

    async def get_definition(self, workflow_id: str, tenant_id: str) -> dict[str, Any]:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
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
        }

    @staticmethod
    def _row_to_step(row: Any) -> dict[str, Any]:
        return {
            "step_id": row["step_id"],
            "step_type": row["step_type"],
            "status": row["status"],
            "output": _as_obj(row["output"]),
            "error": row["error"],
            "started_at": _iso(row["started_at"]),
            "finished_at": _iso(row["completed_at"]),
            "duration_ms": float(row["duration_ms"]) if row["duration_ms"] is not None else None,
        }
