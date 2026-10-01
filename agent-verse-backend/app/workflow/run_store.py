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

# Tenant isolation: every tenant-scoped query in this module carries an explicit
# ``tenant_id = CAST(:tid AS uuid)`` predicate IN ADDITION to the RLS GUC. RLS
# alone does nothing on a SUPERUSER / BYPASSRLS connection (the local compose
# stack ran as one, and ``GET /runs`` returned every tenant's runs).

# Shared run read (get/list): step count and definition name are joined on the
# run's own tenant, so a mismatched row can never leak a name or count.
_RUN_SELECT_TAIL = (
    " (SELECT COUNT(*) FROM workflow_step_results s "
    "   WHERE s.run_id = r.id AND s.tenant_id = r.tenant_id) AS step_count "
    "FROM workflow_runs r "
    "LEFT JOIN workflow_definitions d "
    " ON d.id = r.workflow_id AND d.tenant_id = r.tenant_id "
)

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


def _uuid_or_none(value: Any) -> str | None:
    """``value`` as a canonical UUID string, or ``None`` when it is not one."""
    try:
        return str(uuid.UUID(str(value))) if value else None
    except (ValueError, TypeError, AttributeError):
        return None


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
        run_metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> str | None: ...

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
        output: Any = None,
        error: str | None = None,
        cost_usd: float | None = None,
    ) -> bool: ...

    async def list_step_results(
        self, tenant_id: str, run_id: str
    ) -> list[dict[str, Any]]: ...

    async def get_step_result(
        self, tenant_id: str, run_id: str, step_id: str
    ) -> dict[str, Any] | None: ...

    async def get_retryable_webhooks(
        self, max_attempts: int = 3, *, backoff_seconds: float = 300.0
    ) -> list[dict[str, Any]]: ...

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

    def __init__(self, db_factory: Any, *, system_db_factory: Any | None = None) -> None:
        """Args:
        db_factory: async SQLAlchemy ``async_sessionmaker`` (``app.state.db_session_factory``)
            — the application role; every tenant-scoped method uses it.
        system_db_factory: session factory for the cross-tenant maintenance
            methods ONLY (``get_retryable_webhooks``, ``delete_expired_runs``) —
            the maintenance (BYPASSRLS) role. ``None`` resolves
            ``app.db.session.get_system_session_factory()`` at call time.
        """
        self._db = db_factory
        self._system_db = system_db_factory

    def _system_factory(self) -> Any:
        """Session factory for cross-tenant maintenance — never tenant paths.

        ``system_session`` only works on the maintenance role: on the NOBYPASSRLS
        application role (``self._db`` in production) every statement after it
        fails with "query would be affected by row-level security policy", so
        the webhook DLQ retry and run retention never did anything.
        """
        if self._system_db is not None:
            return self._system_db
        from app.db.session import get_system_session_factory

        return get_system_session_factory()

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
        run_metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        """Insert a ``pending`` run row and return the id of the run that owns it.

        ``run_metadata`` (callback_url, retry lineage, ...) used to be accepted by
        the runner but never written, so an out-of-process worker could not see
        it. It is now persisted.

        With an ``idempotency_key`` the insert is arbitrated by the partial UNIQUE
        index ``uq_workflow_runs_idempotency`` (tenant_id, workflow_id, key): a
        duplicate trigger conflicts and the EXISTING run's id is returned instead
        of ``run_id``. The database (not an in-process check) picks the winner, so
        this holds across API replicas and concurrent client retries.
        """
        from sqlalchemy import text as sa_text

        params: dict[str, Any] = {
            "id": run_id,
            "tenant_id": tenant_id,
            "workflow_id": workflow_id,
            "trigger_type": trigger_type,
            "trigger_payload": json.dumps(trigger_payload) if trigger_payload else None,
            "inputs": json.dumps(inputs or {}),
            "labels": json.dumps(labels or {}),
            "is_test_run": is_test_run,
            "run_metadata": json.dumps(run_metadata or {}, default=str),
            "idempotency_key": idempotency_key or None,
        }
        insert_sql = (
            "INSERT INTO workflow_runs "
            "(id, tenant_id, workflow_id, trigger_type, trigger_payload, inputs, "
            " status, labels, is_test_run, run_metadata, idempotency_key, created_at) "
            "VALUES (:id, CAST(:tenant_id AS uuid), CAST(:workflow_id AS uuid), "
            " :trigger_type, CAST(:trigger_payload AS jsonb), CAST(:inputs AS jsonb), "
            " 'pending', CAST(:labels AS jsonb), :is_test_run, "
            " CAST(:run_metadata AS jsonb), :idempotency_key, NOW())"
        )
        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            if not idempotency_key:
                await session.execute(sa_text(insert_sql), params)
                await session.commit()
                return run_id
            inserted = (
                await session.execute(
                    sa_text(
                        insert_sql
                        + " ON CONFLICT (tenant_id, workflow_id, idempotency_key) "
                        "WHERE idempotency_key IS NOT NULL DO NOTHING RETURNING id"
                    ),
                    params,
                )
            ).first()
            if inserted is not None:
                await session.commit()
                return str(inserted[0])
            existing = (
                await session.execute(
                    sa_text(
                        "SELECT id FROM workflow_runs "
                        "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                        "AND workflow_id = CAST(:workflow_id AS uuid) "
                        "AND idempotency_key = :idempotency_key"
                    ),
                    params,
                )
            ).first()
            await session.commit()
            if existing is None:  # pragma: no cover - conflicting row deleted meanwhile
                raise RuntimeError("idempotent run insert conflicted but no run was found")
            return str(existing[0])

    async def get(self, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "SELECT r.*, d.name AS workflow_name, "
                        f"{_RUN_SELECT_TAIL}"
                        "WHERE r.id = CAST(:rid AS uuid) AND r.tenant_id = CAST(:tid AS uuid)"
                    ),
                    {"rid": run_id, "tid": tenant_id},
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

        clauses = ["r.tenant_id = CAST(:tid AS uuid)"]
        params: dict[str, Any] = {"limit": limit, "offset": offset, "tid": tenant_id}
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
                        f"{_RUN_SELECT_TAIL}"
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
        params: dict[str, Any] = {"status": status_str, "rid": run_id, "tid": tenant_id}
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
                    "WHERE id = CAST(:rid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                ),
                params,
            )
            await session.commit()
            return bool(result.rowcount)

    async def get_workflow_id(self, run_id: str, tenant_id: str | None = None) -> str:
        from sqlalchemy import text as sa_text

        if not tenant_id:
            # No tenant, no read: an unscoped lookup would resolve any tenant's
            # run on a BYPASSRLS connection.
            return ""
        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "SELECT workflow_id FROM workflow_runs "
                        "WHERE id = CAST(:rid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                    ),
                    {"rid": run_id, "tid": tenant_id},
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
                    sa_text(
                        "SELECT status FROM workflow_runs "
                        "WHERE id = CAST(:rid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                    ),
                    {"rid": run_id, "tid": tenant_id},
                )
            ).first()
            return str(row[0]) if row and row[0] is not None else None

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
                        "WHERE id = CAST(:wid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                    ),
                    {"wid": workflow_id, "tid": tenant_id},
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
            # INSERT ... SELECT from the tenant's own run: the run_id FK check
            # ignores RLS, so a plain VALUES insert could attach a step row to
            # another tenant's run.
            result = await session.execute(
                sa_text(
                    "INSERT INTO workflow_step_results "
                    "(id, run_id, tenant_id, step_id, step_type, step_name, status, "
                    " resolved_input, attempt_number, started_at) "
                    "SELECT :id, r.id, r.tenant_id, :step_id, "
                    " :step_type, :step_name, 'running', CAST(:resolved_input AS jsonb), "
                    " :attempt_number, NOW() "
                    "FROM workflow_runs r "
                    "WHERE r.id = CAST(:run_id AS uuid) AND r.tenant_id = CAST(:tenant_id AS uuid)"
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
            if not result.rowcount:
                raise KeyError(f"workflow run {run_id!r} not found for tenant")
            return result_id

    async def record_step_finish(
        self,
        *,
        run_id: str,
        tenant_id: str,
        step_id: str,
        status: Any,
        output: Any = None,
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
                    "WHERE tenant_id = CAST(:tid AS uuid) AND id = ("
                    "  SELECT id FROM workflow_step_results "
                    "  WHERE run_id = CAST(:run_id AS uuid) AND step_id = :step_id "
                    "  AND tenant_id = CAST(:tid AS uuid) "
                    "  ORDER BY attempt_number DESC, started_at DESC LIMIT 1)"
                ),
                {
                    "status": _as_str(status),
                    "output": json.dumps(output) if output is not None else None,
                    "error": error,
                    "cost_usd": cost_usd,
                    "run_id": run_id,
                    "step_id": step_id,
                    "tid": tenant_id,
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
                        "WHERE run_id = CAST(:rid AS uuid) AND tenant_id = CAST(:tid AS uuid) "
                        "ORDER BY started_at ASC NULLS LAST, step_id ASC"
                    ),
                    {"rid": run_id, "tid": tenant_id},
                )
            ).mappings().all()
            return [self._row_to_step(r) for r in rows]

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
                        "WHERE run_id = CAST(:rid AS uuid) AND step_id = :step_id "
                        "AND tenant_id = CAST(:tid AS uuid) "
                        "ORDER BY attempt_number DESC, started_at DESC LIMIT 1"
                    ),
                    {"rid": run_id, "step_id": step_id, "tid": tenant_id},
                )
            ).mappings().first()
            return self._row_to_step(row) if row else None

    async def copy_completed_step_results(
        self, tenant_id: str, from_run_id: str, to_run_id: str
    ) -> int:
        """Seed ``to_run_id`` with the COMPLETE step results of ``from_run_id``.

        Used by retry. The engine's node_fn returns a step's persisted output
        instead of re-executing it when that step is already COMPLETE for the
        run, so copying the failed run's completed steps into the retry run makes
        the retry continue after them instead of redoing them. Only the latest
        attempt per step is copied.
        """
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            result = await session.execute(
                sa_text(
                    "INSERT INTO workflow_step_results "
                    "(id, run_id, tenant_id, step_id, step_type, step_name, status, "
                    " resolved_input, output, attempt_number, started_at, completed_at, "
                    " duration_ms) "
                    "SELECT gen_random_uuid(), CAST(:to_run AS uuid), s.tenant_id, s.step_id, "
                    " s.step_type, s.step_name, s.status, s.resolved_input, s.output, 1, "
                    " s.started_at, s.completed_at, s.duration_ms "
                    "FROM ("
                    "  SELECT DISTINCT ON (step_id) * FROM workflow_step_results "
                    "  WHERE run_id = CAST(:from_run AS uuid) AND tenant_id = CAST(:tid AS uuid) "
                    "  ORDER BY step_id, attempt_number DESC, started_at DESC"
                    ") s WHERE s.status = 'complete' AND s.output IS NOT NULL "
                    # The target run must be the same tenant's too.
                    "AND EXISTS (SELECT 1 FROM workflow_runs t "
                    "  WHERE t.id = CAST(:to_run AS uuid) AND t.tenant_id = CAST(:tid AS uuid))"
                ),
                {"from_run": from_run_id, "to_run": to_run_id, "tid": tenant_id},
            )
            await session.commit()
            return int(result.rowcount or 0)

    # ── Durable timer waits ───────────────────────────────────────────────────
    async def get_timer_wait(self, tenant_id: str, run_id: str, step_id: str) -> str | None:
        """Return the persisted ISO wake time for ``step_id`` of a run, or None."""
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "SELECT run_metadata -> 'timer_waits' ->> :step_id "
                        "FROM workflow_runs "
                        "WHERE id = CAST(:rid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                    ),
                    {"rid": run_id, "step_id": step_id, "tid": tenant_id},
                )
            ).first()
            return str(row[0]) if row and row[0] else None

    async def set_timer_wait(
        self, tenant_id: str, run_id: str, step_id: str, wake_at: datetime
    ) -> None:
        """Persist a timer wait. ``run_metadata.timer_waits[step_id]`` keeps the
        step's own wake time (stable across re-dispatches); the ``wake_at`` column
        holds the EARLIEST pending wake, which the beat scan keys on."""
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            await session.execute(
                sa_text(
                    "UPDATE workflow_runs SET "
                    " run_metadata = COALESCE(run_metadata, '{}'::jsonb) || "
                    "   jsonb_build_object('timer_waits', "
                    "     COALESCE(run_metadata -> 'timer_waits', '{}'::jsonb) || "
                    "     jsonb_build_object(CAST(:step_id AS text), CAST(:iso AS text))), "
                    " wake_at = LEAST(COALESCE(wake_at, :ts), :ts) "
                    "WHERE id = CAST(:rid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                ),
                {
                    "rid": run_id,
                    "step_id": step_id,
                    "iso": wake_at.isoformat(),
                    "ts": wake_at,
                    "tid": tenant_id,
                },
            )
            await session.commit()

    async def claim_due_timer_waits(self, limit: int = 200) -> list[dict[str, Any]]:
        """Atomically claim runs whose timer wait is due (cross-tenant beat scan).

        ``FOR UPDATE SKIP LOCKED`` plus flipping ``waiting_timer`` -> ``pending``
        in one statement means two beat replicas (or overlapping scans) never
        claim, and so never re-dispatch, the same run twice. ``wake_at`` is
        cleared so a later re-suspend records a fresh earliest wake.
        """
        from sqlalchemy import text as sa_text

        from app.db.rls import system_session

        system_db = self._system_factory()
        async with system_db() as session, session.begin(), system_session(session):
            rows = (
                await session.execute(
                    sa_text(
                        "UPDATE workflow_runs r SET status = 'pending', wake_at = NULL "
                        "FROM ("
                        "  SELECT id FROM workflow_runs "
                        "  WHERE status = 'waiting_timer' AND wake_at <= NOW() "
                        "  ORDER BY wake_at LIMIT :lim FOR UPDATE SKIP LOCKED"
                        ") due WHERE r.id = due.id "
                        "RETURNING r.id, r.tenant_id, r.workflow_id, r.is_test_run"
                    ),
                    {"lim": limit},
                )
            ).mappings().all()
            return [
                {
                    "run_id": str(r["id"]),
                    "tenant_id": str(r["tenant_id"]),
                    "workflow_id": str(r["workflow_id"]) if r["workflow_id"] else "",
                    "is_test_run": bool(r["is_test_run"] or False),
                }
                for r in rows
            ]

    async def list_stalled_runs(
        self, *, stall_seconds: float, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Runs ``running``/``pending`` with no activity for ``stall_seconds``
        (cross-tenant maintenance scan, WF-22).

        Activity = the run's start/creation, its latest step start/finish, or its
        last stuck re-dispatch. The caller still checks the run's execution
        lease: a live lease means a worker is on a long step, not dead.
        """
        from sqlalchemy import text as sa_text

        from app.db.rls import system_session

        system_db = self._system_factory()
        async with system_db() as session, session.begin(), system_session(session):
            rows = (
                await session.execute(
                    sa_text(
                        "SELECT r.id, r.tenant_id, r.workflow_id FROM workflow_runs r "
                        "WHERE r.status IN ('running', 'pending') "
                        " AND NOT COALESCE(r.is_test_run, FALSE) "
                        " AND GREATEST("
                        "   COALESCE(r.started_at, r.created_at), "
                        "   COALESCE((SELECT MAX(COALESCE(s.completed_at, s.started_at)) "
                        "     FROM workflow_step_results s WHERE s.run_id = r.id), r.created_at), "
                        "   COALESCE((r.run_metadata->>'stuck_redispatched_at')::timestamptz, "
                        "     r.created_at)"
                        " ) < NOW() - make_interval(secs => :stall) "
                        "ORDER BY r.created_at LIMIT :lim"
                    ),
                    {"stall": stall_seconds, "lim": limit},
                )
            ).mappings().all()
            return [
                {
                    "run_id": str(r["id"]),
                    "tenant_id": str(r["tenant_id"]),
                    "workflow_id": str(r["workflow_id"]) if r["workflow_id"] else "",
                }
                for r in rows
            ]

    async def mark_stuck_redispatched(self, tenant_id: str, run_id: str) -> None:
        """Stamp a stuck re-dispatch so the run counts as active again."""
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            await session.execute(
                sa_text(
                    "UPDATE workflow_runs SET run_metadata = COALESCE(run_metadata, '{}'::jsonb)"
                    " || jsonb_build_object('stuck_redispatched_at', NOW()) "
                    "WHERE id = CAST(:rid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                ),
                {"rid": run_id, "tid": tenant_id},
            )
            await session.commit()

    async def release_timer_claim(self, tenant_id: str, run_id: str) -> None:
        """Undo a claim whose re-dispatch failed: back to ``waiting_timer`` and due
        now, so the next beat scan retries instead of stranding it ``pending``."""
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            await session.execute(
                sa_text(
                    "UPDATE workflow_runs SET status = 'waiting_timer', wake_at = NOW() "
                    "WHERE id = CAST(:rid AS uuid) AND status = 'pending' "
                    "AND tenant_id = CAST(:tid AS uuid)"
                ),
                {"rid": run_id, "tid": tenant_id},
            )
            await session.commit()

    # ── Durable event waits (wait-on-event / emit_event) ─────────────────────
    # A waiting run records ``run_metadata.event_waits[step_id] = channel`` plus
    # a timer wait at its timeout deadline, and suspends as ``waiting_timer``.
    # ``deliver_event`` stores the payload in ``event_deliveries[step_id]`` and
    # makes the run due NOW, so the existing timer beat re-dispatches it. Both
    # run under the tenant GUC: an emit can only wake the same tenant's runs.
    async def register_event_wait(
        self, tenant_id: str, run_id: str, step_id: str, channel: str, deadline: datetime
    ) -> None:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            await session.execute(
                sa_text(
                    "UPDATE workflow_runs SET "
                    " run_metadata = COALESCE(run_metadata, '{}'::jsonb) || jsonb_build_object("
                    "   'event_waits', COALESCE(run_metadata -> 'event_waits', '{}'::jsonb) "
                    "     || jsonb_build_object(CAST(:step_id AS text), CAST(:ch AS text)), "
                    "   'timer_waits', COALESCE(run_metadata -> 'timer_waits', '{}'::jsonb) "
                    "     || jsonb_build_object(CAST(:step_id AS text), CAST(:iso AS text))), "
                    " wake_at = LEAST(COALESCE(wake_at, :ts), :ts) "
                    "WHERE id = CAST(:rid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                ),
                {
                    "rid": run_id,
                    "tid": tenant_id,
                    "step_id": step_id,
                    "ch": channel,
                    "iso": deadline.isoformat(),
                    "ts": deadline,
                },
            )
            await session.commit()

    async def get_event_delivery(
        self, tenant_id: str, run_id: str, step_id: str
    ) -> dict[str, Any] | None:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "SELECT run_metadata -> 'event_deliveries' -> :step_id "
                        "FROM workflow_runs "
                        "WHERE id = CAST(:rid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                    ),
                    {"rid": run_id, "step_id": step_id, "tid": tenant_id},
                )
            ).first()
        if not row or row[0] is None:
            return None
        val = _as_obj(row[0])
        return val if isinstance(val, dict) else {"value": val}

    async def clear_event_wait(self, tenant_id: str, run_id: str, step_id: str) -> None:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            await session.execute(
                sa_text(
                    "UPDATE workflow_runs SET run_metadata = jsonb_set("
                    " COALESCE(run_metadata, '{}'::jsonb), '{event_waits}', "
                    " COALESCE(run_metadata -> 'event_waits', '{}'::jsonb) "
                    "   - CAST(:step_id AS text))"
                    " WHERE id = CAST(:rid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                ),
                {"rid": run_id, "step_id": step_id, "tid": tenant_id},
            )
            await session.commit()

    async def deliver_event(self, tenant_id: str, channel: str, payload: dict[str, Any]) -> int:
        """Deliver ``payload`` to every run of the tenant waiting on ``channel``.

        Returns the number of waiting steps woken. The waiter entry is removed in
        the same statement, so an event is delivered to a given wait only once.
        """
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            rows = (
                await session.execute(
                    sa_text(
                        # Aggregated per run so a run with several steps waiting
                        # on the same channel gets every one of them delivered.
                        "WITH w AS ("
                        "  SELECT r.id, "
                        "   jsonb_object_agg(e.key, CAST(:payload AS jsonb)) AS deliv, "
                        "   array_agg(e.key) AS steps "
                        "  FROM workflow_runs r, "
                        "   jsonb_each_text(COALESCE(r.run_metadata -> 'event_waits', "
                        "     '{}'::jsonb)) e "
                        # Not only 'waiting_timer': a run that registered its wait
                        # but has not yet persisted the suspension (or is being
                        # re-dispatched) must not miss the event.
                        "  WHERE r.tenant_id = CAST(:tid AS uuid) "
                        "    AND r.status IN ('waiting_timer', 'running', 'pending') "
                        "    AND e.value = :ch "
                        "  GROUP BY r.id"
                        ") "
                        "UPDATE workflow_runs r SET "
                        " run_metadata = jsonb_set(jsonb_set("
                        "   COALESCE(r.run_metadata, '{}'::jsonb), '{event_deliveries}', "
                        "   COALESCE(r.run_metadata -> 'event_deliveries', '{}'::jsonb) "
                        "     || w.deliv), "
                        "   '{event_waits}', "
                        "   COALESCE(r.run_metadata -> 'event_waits', '{}'::jsonb) - w.steps), "
                        " wake_at = NOW() "
                        "FROM w WHERE r.id = w.id RETURNING cardinality(w.steps)"
                    ),
                    {"tid": tenant_id, "ch": channel, "payload": json.dumps(payload, default=str)},
                )
            ).all()
            await session.commit()
            return sum(int(r[0] or 0) for r in rows)

    # ── Maintenance (cross-tenant) ────────────────────────────────────────────
    async def get_retryable_webhooks(
        self, max_attempts: int = 3, *, backoff_seconds: float = 300.0
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text as sa_text

        from app.db.rls import system_session

        system_db = self._system_factory()
        async with system_db() as session, session.begin(), system_session(session):
            rows = (
                await session.execute(
                    sa_text(
                        "SELECT id, tenant_id, workflow_id, payload FROM workflow_webhook_events "
                        "WHERE status IN ('pending', 'failed') AND attempts < :max_attempts "
                        # Exponential backoff: ``base`` after the delivery and
                        # after the 1st retry, then 2x, 4x, ... per failed retry.
                        " AND COALESCE(last_attempted_at, received_at) <= NOW() - "
                        "   make_interval(secs => :base * power(2, GREATEST(attempts - 1, 0))) "
                        "ORDER BY received_at ASC LIMIT 100"
                    ),
                    {"max_attempts": max_attempts, "base": backoff_seconds},
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

    async def delete_expired_runs(self, *, batch: int = 2000, max_batches: int = 500) -> int:
        """Delete finished runs past their retention (step results cascade).

        Three fixes: (1) only TERMINAL runs are eligible — a run waiting on a
        human approval for longer than the retention window used to be deleted
        mid-flight; (2) a definition without ``run_retention_days`` (and runs
        with no definition) now falls back to the platform retention instead of
        keeping every run and its step results forever; (3) deletes run in
        bounded batches, each its own short transaction, instead of one
        unbounded DELETE cascading over workflow_step_results.
        """
        from sqlalchemy import text as sa_text

        from app.core.config import get_settings
        from app.db.rls import system_session

        default_days = int(getattr(get_settings(), "data_retention_days", 90) or 90)
        system_db = self._system_factory()
        total = 0
        for _ in range(max_batches):
            async with system_db() as session, session.begin(), system_session(session):
                result = await session.execute(
                    sa_text(
                        "DELETE FROM workflow_runs WHERE id IN ("
                        " SELECT r.id FROM workflow_runs r"
                        " LEFT JOIN workflow_definitions d ON d.id = r.workflow_id"
                        " WHERE r.status IN ("
                        "   'complete', 'failed', 'cancelled', 'timed_out')"
                        " AND COALESCE(r.completed_at, r.created_at) < NOW()"
                        "   - make_interval(days => COALESCE(d.run_retention_days, :dflt))"
                        " LIMIT :lim)"
                    ),
                    {"dflt": default_days, "lim": batch},
                )
            deleted = int(result.rowcount or 0)
            total += deleted
            if deleted < batch:
                break
        return total

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
                        # Explicit tenant predicate as well as RLS: a BYPASSRLS
                        # connection must not read another tenant's history.
                        "AND tenant_id = CAST(:tid AS uuid) "
                        "ORDER BY published_at DESC"
                    ),
                    {"wid": workflow_id, "tid": tenant_id},
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
                        "SELECT version, definition_yaml, definition_json, change_summary, "
                        "published_by, published_at "
                        "FROM workflow_definition_versions "
                        "WHERE workflow_id = CAST(:wid AS uuid) AND version = :ver "
                        "AND tenant_id = CAST(:tid AS uuid)"
                    ),
                    {"wid": workflow_id, "ver": version, "tid": tenant_id},
                )
            ).mappings().first()
            if row is None:
                return None
            return {
                "version": row["version"],
                "definition_yaml": row["definition_yaml"],
                "definition_json": _as_obj(row["definition_json"]) or {},
                "change_summary": row.get("change_summary"),
                "published_by": str(row["published_by"]) if row.get("published_by") else None,
                "published_at": _iso(row.get("published_at")),
            }

    async def record_definition_version(
        self,
        tenant_id: str,
        workflow_id: str,
        *,
        version: str,
        definition: dict[str, Any],
        published_by: str | None = None,
        change_summary: str | None = None,
    ) -> dict[str, Any]:
        """Snapshot a published definition into ``workflow_definition_versions``.

        Raises on failure (the caller rolls the publish back): a publish whose
        version was not recorded could never be restored or diffed.
        ``published_by`` is stored only when it is a UUID (API key ids are);
        otherwise the column keeps NULL-equivalent semantics via the default.
        """
        import yaml as _yaml  # type: ignore[import-untyped]
        from sqlalchemy import text as sa_text

        by = _uuid_or_none(published_by)
        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "INSERT INTO workflow_definition_versions "
                        "(workflow_id, tenant_id, version, definition_yaml, definition_json, "
                        " change_summary, published_by) "
                        "VALUES (CAST(:wid AS uuid), CAST(:tid AS uuid), :ver, :yaml, "
                        " CAST(:def AS jsonb), :summary, "
                        " COALESCE(CAST(:by AS uuid), gen_random_uuid())) "
                        "RETURNING published_at"
                    ),
                    {
                        "wid": workflow_id,
                        "tid": tenant_id,
                        "ver": version,
                        "yaml": _yaml.safe_dump(definition or {}, sort_keys=False),
                        "def": json.dumps(definition or {}),
                        "summary": change_summary,
                        "by": by,
                    },
                )
            ).first()
            await session.commit()
        return {
            "version": version,
            "definition_json": definition or {},
            "change_summary": change_summary,
            "published_by": by,
            "published_at": _iso(row[0]) if row else None,
        }

    # ── Publish approval (workflow_definitions approval columns) ──────────────
    # ``requires_publish_approval`` / ``publish_approved_*`` are real columns
    # (migration 0108); the pending submission (who submitted which version)
    # rides in ``trigger_config.publish_submission`` so no migration is needed.
    async def get_publish_approval(
        self, tenant_id: str, workflow_id: str
    ) -> dict[str, Any] | None:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "SELECT COALESCE(requires_publish_approval, FALSE) AS required, "
                        "trigger_config->'publish_submission' AS submission, "
                        "publish_approved_by, publish_approved_at, publish_approval_note "
                        "FROM workflow_definitions "
                        "WHERE id = CAST(:wid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                    ),
                    {"wid": workflow_id, "tid": tenant_id},
                )
            ).mappings().first()
        if row is None:
            return None
        submission = _as_obj(row["submission"])
        return {
            "requires_publish_approval": bool(row["required"]),
            "submission": submission if isinstance(submission, dict) else None,
            "approved_by": (
                str(row["publish_approved_by"]) if row["publish_approved_by"] else None
            ),
            "approved_at": _iso(row["publish_approved_at"]),
            "note": row["publish_approval_note"],
        }

    async def set_requires_publish_approval(
        self, tenant_id: str, workflow_id: str, required: bool
    ) -> bool:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            result = await session.execute(
                sa_text(
                    "UPDATE workflow_definitions SET requires_publish_approval = :req, "
                    "updated_at = NOW() "
                    "WHERE id = CAST(:wid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                ),
                {"wid": workflow_id, "req": bool(required), "tid": tenant_id},
            )
            await session.commit()
            return int(result.rowcount or 0) > 0

    async def set_publish_submission(
        self, tenant_id: str, workflow_id: str, submission: dict[str, Any] | None
    ) -> bool:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            if submission is None:
                stmt = (
                    "UPDATE workflow_definitions SET trigger_config = "
                    "COALESCE(trigger_config, '{}'::jsonb) - 'publish_submission', "
                    "updated_at = NOW() "
                    "WHERE id = CAST(:wid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                )
                params: dict[str, Any] = {"wid": workflow_id, "tid": tenant_id}
            else:
                stmt = (
                    "UPDATE workflow_definitions SET trigger_config = jsonb_set("
                    "COALESCE(trigger_config, '{}'::jsonb), '{publish_submission}', "
                    "CAST(:sub AS jsonb)), updated_at = NOW() "
                    "WHERE id = CAST(:wid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                )
                params = {"wid": workflow_id, "sub": json.dumps(submission), "tid": tenant_id}
            result = await session.execute(sa_text(stmt), params)
            await session.commit()
            return int(result.rowcount or 0) > 0

    async def record_publish_approval(
        self, tenant_id: str, workflow_id: str, *, approved_by: str, note: str
    ) -> bool:
        """Stamp the approver/note and clear the pending submission, atomically."""
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            result = await session.execute(
                sa_text(
                    "UPDATE workflow_definitions SET "
                    "publish_approved_by = CAST(:by AS uuid), publish_approved_at = NOW(), "
                    "publish_approval_note = :note, trigger_config = "
                    "COALESCE(trigger_config, '{}'::jsonb) - 'publish_submission', "
                    "updated_at = NOW() "
                    "WHERE id = CAST(:wid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                ),
                {
                    "wid": workflow_id,
                    "by": _uuid_or_none(approved_by),
                    "note": note or "",
                    "tid": tenant_id,
                },
            )
            await session.commit()
            return int(result.rowcount or 0) > 0

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
                        "WHERE workflow_id = CAST(:wid AS uuid) AND tenant_id = CAST(:tid AS uuid) "
                        "ORDER BY granted_at DESC"
                    ),
                    {"wid": workflow_id, "tid": tenant_id},
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
                        # INSERT ... SELECT from the tenant's own definition (the
                        # workflow_id FK ignores RLS) and never update another
                        # tenant's grant on conflict.
                        "INSERT INTO workflow_permissions "
                        "  (workflow_id, tenant_id, subject_type, subject_id, permission) "
                        "SELECT d.id, d.tenant_id, :st, :sid, :perm "
                        "FROM workflow_definitions d "
                        "WHERE d.id = CAST(:wid AS uuid) AND d.tenant_id = CAST(:tid AS uuid) "
                        "ON CONFLICT (workflow_id, subject_type, subject_id, permission) "
                        "  DO UPDATE SET granted_at = NOW() "
                        "  WHERE workflow_permissions.tenant_id = EXCLUDED.tenant_id "
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
            if row is None:
                raise KeyError(f"workflow definition {workflow_id!r} not found")
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
                    "WHERE id = CAST(:pid AS uuid) AND workflow_id = CAST(:wid AS uuid) "
                    "AND tenant_id = CAST(:tid AS uuid)"
                ),
                {"pid": permission_id, "wid": workflow_id, "tid": tenant_id},
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
                        + "AND tenant_id = CAST(:tid AS uuid) "
                        + "AND created_at >= NOW() - make_interval(days => :days)"
                    ),
                    {"wid": workflow_id, "days": int(days), "tid": tenant_id},
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
                        + "WHERE tenant_id = CAST(:tid AS uuid) "
                        + "AND created_at >= NOW() - make_interval(days => :days)"
                    ),
                    {"days": int(days), "tid": tenant_id},
                )
            ).mappings().first()
            return self._stats_row(row)

    # ── Webhook token version (workflow_definitions.trigger_config) ───────────
    async def get_webhook_token_version(self, tenant_id: str, workflow_id: str) -> int:
        """The workflow's current webhook token version (0 = never rotated)."""
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "SELECT COALESCE((trigger_config->>'webhook_token_version')::int, 0) "
                        "FROM workflow_definitions "
                        "WHERE id = CAST(:wid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                    ),
                    {"wid": workflow_id, "tid": tenant_id},
                )
            ).first()
            return int(row[0]) if row else 0

    async def rotate_webhook_token(self, tenant_id: str, workflow_id: str) -> int:
        """Bump the token version (invalidating every earlier URL); return it."""
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "UPDATE workflow_definitions SET trigger_config = jsonb_set("
                        " COALESCE(trigger_config, '{}'::jsonb), '{webhook_token_version}', "
                        " to_jsonb(COALESCE((trigger_config->>'webhook_token_version')::int, 0)"
                        " + 1)), updated_at = NOW() "
                        "WHERE id = CAST(:wid AS uuid) AND tenant_id = CAST(:tid AS uuid) "
                        "RETURNING (trigger_config->>'webhook_token_version')::int"
                    ),
                    {"wid": workflow_id, "tid": tenant_id},
                )
            ).first()
            await session.commit()
            if row is None:
                raise KeyError(f"workflow definition {workflow_id!r} not found")
            return int(row[0])

    # ── Webhook events (workflow_webhook_events) ──────────────────────────────
    async def record_webhook_failure(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        token_fingerprint: str,
        payload: dict[str, Any],
        error: str,
    ) -> str:
        """Dead-letter a webhook delivery whose run could not be started.

        ``attempts`` starts at 0; the retry task owns every later attempt. Only a
        fingerprint of the webhook token is stored — the token is a credential.
        """
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "INSERT INTO workflow_webhook_events "
                        "(tenant_id, workflow_id, webhook_token, payload, status, attempts, "
                        " last_error) "
                        "VALUES (CAST(:tid AS uuid), CAST(:wid AS uuid), :token, "
                        " CAST(:payload AS jsonb), 'failed', 0, :error) RETURNING id"
                    ),
                    {
                        "tid": tenant_id,
                        "wid": workflow_id,
                        "token": token_fingerprint,
                        "payload": json.dumps(payload, default=str),
                        "error": error[:2000],
                    },
                )
            ).first()
            await session.commit()
            return str(row[0]) if row else ""

    async def mark_webhook_attempt(
        self,
        *,
        tenant_id: str,
        event_id: str,
        run_id: str | None = None,
        error: str | None = None,
        max_attempts: int = 3,
    ) -> str | None:
        """Record one retry of a dead-lettered delivery and return its new status.

        Success (``run_id`` set) → ``succeeded``; a failure → ``failed``, or
        ``dead`` once ``max_attempts`` is reached — so a row is never retried
        forever and a succeeded row is never retried again. Guarded on the row
        still being retryable, so two overlapping retry runs cannot both count.
        """
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            row = (
                await session.execute(
                    sa_text(
                        "UPDATE workflow_webhook_events SET "
                        " attempts = attempts + 1, last_attempted_at = NOW(), "
                        " status = CASE WHEN :ok THEN 'succeeded' "
                        "   WHEN attempts + 1 >= :max THEN 'dead' ELSE 'failed' END, "
                        " run_id = CASE WHEN :ok THEN CAST(:run_id AS uuid) ELSE run_id END, "
                        " last_error = CASE WHEN :ok THEN last_error ELSE :error END, "
                        " completed_at = CASE WHEN :ok THEN NOW() ELSE completed_at END "
                        "WHERE id = CAST(:eid AS uuid) AND status IN ('pending', 'failed') "
                        "AND tenant_id = CAST(:tid AS uuid) "
                        "RETURNING status"
                    ),
                    {
                        "tid": tenant_id,
                        "ok": run_id is not None,
                        "run_id": run_id,
                        "error": (error or "")[:2000],
                        "max": max_attempts,
                        "eid": event_id,
                    },
                )
            ).first()
            await session.commit()
            return str(row[0]) if row else None

    async def list_webhook_events(
        self, tenant_id: str, workflow_id: str, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        from sqlalchemy import text as sa_text

        async with self._db() as session:
            await self._set_tenant(session, tenant_id)
            params = {"wid": workflow_id, "limit": limit, "offset": offset, "tid": tenant_id}
            total = (
                await session.execute(
                    sa_text(
                        "SELECT COUNT(*) FROM workflow_webhook_events "
                        "WHERE workflow_id = CAST(:wid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                    ),
                    {"wid": workflow_id, "tid": tenant_id},
                )
            ).scalar_one()
            rows = (
                await session.execute(
                    sa_text(
                        "SELECT id, webhook_token, status, attempts, last_error, run_id, "
                        " received_at, last_attempted_at, completed_at "
                        "FROM workflow_webhook_events "
                        "WHERE workflow_id = CAST(:wid AS uuid) AND tenant_id = CAST(:tid AS uuid) "
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
            "error_step_id": row.get("error_step_id"),
            "started_at": _iso(row["started_at"]),
            "finished_at": _iso(row["completed_at"]),
            "duration_ms": _duration_ms(row["started_at"], row["completed_at"]),
            "step_count": int(row.get("step_count") or 0),
            "cost_usd": float(row["cost_usd"] or 0),
            "tokens_used": int(row.get("tokens_used") or 0),
            "trigger_type": row.get("trigger_type"),
            "is_test_run": bool(row.get("is_test_run") or False),
            "labels": _as_obj(row.get("labels")) or {},
            # Read by the out-of-process worker (callback_url, retry lineage).
            "run_metadata": _as_obj(row.get("run_metadata")) or {},
            "idempotency_key": row.get("idempotency_key"),
            "wake_at": _iso(row.get("wake_at")),
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
            "error_step_id": row.get("error_step_id"),
            "started_at": _iso(row["started_at"]),
            "finished_at": _iso(row["completed_at"]),
            "duration_ms": float(row["duration_ms"]) if row["duration_ms"] is not None else None,
        }
