"""Tenant-scoped, durable storage for eval suites, golden tasks and suite runs.

The eval-suite API used to keep everything in ``EvalSuiteRunner``'s process
dicts, keyed by suite id alone and shared by every tenant:

* any tenant could list every other tenant's suites, append golden tasks to
  them, run them and read their results;
* a suite created on one replica did not exist on the others, and a restart
  erased all suites and all run history;
* ``POST /run`` executed every golden goal inside the HTTP request (up to 60 s
  per task, sequentially), so a ten-task suite held the connection for ten
  minutes and every proxy timed it out.

Suites and runs are now rows in ``eval_suites`` / ``eval_suite_results`` under
FORCE'd RLS, keyed ``(tenant_id, id)``, and every statement also carries an
explicit tenant predicate. A run is recorded as ``running`` first and finished
by a background task; a ``running`` row whose worker died is reported as
``abandoned`` once it is older than :data:`STALE_RUN_AFTER`.

Without a database (unit tests / in-memory dev mode) the store falls back to
process-local dicts keyed by tenant — never by suite id alone.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import text as sa_text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.intelligence.eval_suite import EvalSuiteResult, GoldenTask

__all__ = ["STALE_RUN_AFTER", "EvalSuiteStore", "task_from_dict", "task_to_dict"]

# A run still "running" after this long lost its worker (replica restart/crash).
STALE_RUN_AFTER = timedelta(hours=1)

_MEM_SUITES: dict[str, dict[str, dict[str, Any]]] = {}
_MEM_RUNS: dict[str, dict[str, dict[str, Any]]] = {}


def task_to_dict(task: GoldenTask) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "goal": task.goal,
        "expected_tools": list(task.expected_tools),
        "forbidden_tools": list(task.forbidden_tools),
        "expected_output_contains": list(task.expected_output_contains),
        "expected_output": task.expected_output or "",
        "min_score": task.min_score,
        "max_iterations": task.max_iterations,
        "tags": list(task.tags),
    }


def task_from_dict(suite_id: str, data: dict[str, Any]) -> GoldenTask:
    from app.intelligence.eval_suite import GoldenTask

    return GoldenTask(
        task_id=str(data.get("task_id") or ""),
        suite_id=suite_id,
        goal=str(data.get("goal", "")),
        expected_tools=list(data.get("expected_tools") or []),
        forbidden_tools=list(data.get("forbidden_tools") or []),
        expected_output_contains=list(data.get("expected_output_contains") or []),
        expected_output=str(data.get("expected_output") or "") or None,
        min_score=float(data["min_score"]) if data.get("min_score") is not None else 0.8,
        max_iterations=int(data.get("max_iterations") or 15),
        tags=list(data.get("tags") or []),
    )


def _loads(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


@asynccontextmanager
async def _scoped(session_factory: Any, tenant_id: str) -> AsyncIterator[AsyncSession]:
    from app.db.rls import sqlalchemy_rls_context

    async with (
        session_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        yield session


class EvalSuiteStore:
    """One tenant's eval suites and their run history."""

    def __init__(self, session_factory: Any, tenant_id: str) -> None:
        self._db = session_factory
        self._tenant_id = str(tenant_id)

    # ── suites ────────────────────────────────────────────────────────────────

    async def create(self, suite_id: str, *, name: str, description: str) -> dict[str, Any] | None:
        """Create a suite; ``None`` when this tenant already has that id."""
        now = datetime.now(UTC)
        if self._db is None:
            suites = _MEM_SUITES.setdefault(self._tenant_id, {})
            if suite_id in suites:
                return None
            suites[suite_id] = {
                "suite_id": suite_id, "name": name, "description": description,
                "tasks": [], "created_at": now.isoformat(),
            }
            return self._summary(suites[suite_id])
        async with _scoped(self._db, self._tenant_id) as s:
            row = (
                await s.execute(
                    sa_text(
                        "INSERT INTO eval_suites (id, tenant_id, name, description, tasks) "
                        "VALUES (:id, :tid, :name, :description, CAST('[]' AS json)) "
                        "ON CONFLICT (tenant_id, id) DO NOTHING RETURNING created_at"
                    ),
                    {"id": suite_id, "tid": self._tenant_id, "name": name,
                     "description": description},
                )
            ).first()
        if row is None:
            return None
        return {"suite_id": suite_id, "name": name, "description": description,
                "task_count": 0, "created_at": _iso(row[0])}

    @staticmethod
    def _summary(suite: dict[str, Any]) -> dict[str, Any]:
        return {
            "suite_id": suite["suite_id"],
            "name": suite["name"],
            "description": suite.get("description", ""),
            "task_count": len(suite.get("tasks", [])),
            "created_at": suite.get("created_at", ""),
        }

    async def list(self) -> list[dict[str, Any]]:
        if self._db is None:
            return [self._summary(v) for v in _MEM_SUITES.get(self._tenant_id, {}).values()]
        async with _scoped(self._db, self._tenant_id) as s:
            rows = (
                await s.execute(
                    sa_text(
                        "SELECT id, name, description, json_array_length(tasks) AS n, created_at "
                        "FROM eval_suites WHERE tenant_id = :tid "
                        "ORDER BY created_at DESC, id LIMIT 500"
                    ),
                    {"tid": self._tenant_id},
                )
            ).all()
        return [
            {"suite_id": r[0], "name": r[1], "description": r[2] or "", "task_count": int(r[3]),
             "created_at": _iso(r[4])}
            for r in rows
        ]

    async def get(self, suite_id: str) -> dict[str, Any] | None:
        """Suite metadata plus its task dicts, or ``None``."""
        if self._db is None:
            suite = _MEM_SUITES.get(self._tenant_id, {}).get(suite_id)
            if suite is None:
                return None
            return {**self._summary(suite), "tasks": [dict(t) for t in suite["tasks"]]}
        async with _scoped(self._db, self._tenant_id) as s:
            row = (
                await s.execute(
                    sa_text(
                        "SELECT id, name, description, tasks, created_at FROM eval_suites "
                        "WHERE tenant_id = :tid AND id = :id"
                    ),
                    {"tid": self._tenant_id, "id": suite_id},
                )
            ).first()
        if row is None:
            return None
        tasks = _loads(row[3]) or []
        return {"suite_id": row[0], "name": row[1], "description": row[2] or "",
                "task_count": len(tasks), "created_at": _iso(row[4]), "tasks": tasks}

    async def add_task(self, suite_id: str, task: dict[str, Any]) -> bool:
        """Append a golden task atomically; ``False`` when the suite is not this tenant's."""
        if self._db is None:
            suite = _MEM_SUITES.get(self._tenant_id, {}).get(suite_id)
            if suite is None:
                return False
            suite["tasks"].append(dict(task))
            return True
        async with _scoped(self._db, self._tenant_id) as s:
            # Server-side append: concurrent adds from different replicas cannot
            # overwrite each other (no read-modify-write).
            result = await s.execute(
                sa_text(
                    "UPDATE eval_suites SET tasks = CAST(tasks::jsonb || "
                    " jsonb_build_array(CAST(:task AS jsonb)) AS json), updated_at = now() "
                    "WHERE tenant_id = :tid AND id = :id"
                ),
                {"tid": self._tenant_id, "id": suite_id, "task": json.dumps(task)},
            )
        return bool(getattr(result, "rowcount", 0))

    async def delete(self, suite_id: str) -> bool:
        if self._db is None:
            _MEM_RUNS.get(self._tenant_id, {}).pop(suite_id, None)
            return _MEM_SUITES.get(self._tenant_id, {}).pop(suite_id, None) is not None
        async with _scoped(self._db, self._tenant_id) as s:
            result = await s.execute(
                sa_text("DELETE FROM eval_suites WHERE tenant_id = :tid AND id = :id"),
                {"tid": self._tenant_id, "id": suite_id},
            )
            await s.execute(
                sa_text("DELETE FROM eval_suite_results WHERE tenant_id = :tid AND suite_id = :id"),
                {"tid": self._tenant_id, "id": suite_id},
            )
        return bool(getattr(result, "rowcount", 0))

    # ── runs ──────────────────────────────────────────────────────────────────

    async def start_run(self, suite_id: str, run_id: str, total: int) -> None:
        if self._db is None:
            _MEM_RUNS.setdefault(self._tenant_id, {}).setdefault(suite_id, {})[run_id] = {
                "run_id": run_id, "status": "running", "total": total, "passed": 0,
                "failed": 0, "pass_rate": 0.0, "task_results": [], "error": None,
                "run_at": datetime.now(UTC).isoformat(), "finished_at": None,
            }
            return
        async with _scoped(self._db, self._tenant_id) as s:
            await s.execute(
                sa_text(
                    "INSERT INTO eval_suite_results (id, suite_id, tenant_id, run_id, "
                    " total_tasks, status) VALUES (:id, :sid, :tid, :id, :total, 'running')"
                ),
                {"id": run_id, "sid": suite_id, "tid": self._tenant_id, "total": total},
            )

    async def finish_run(
        self,
        suite_id: str,
        run_id: str,
        *,
        result: EvalSuiteResult | None,
        error: str | None = None,
    ) -> None:
        task_results = [
            {
                "task_id": r.task_id,
                "passed": r.passed,
                "status": r.status,
                "failure_reasons": r.failure_reasons,
                "duration_seconds": round(r.duration_seconds, 2),
                "goal_id": r.goal_id,
                "terminal_event": r.terminal_event,
                "score": r.score,
                "judge": r.judge,
            }
            for r in (result.task_results if result is not None else [])
        ]
        values = {
            "status": "failed" if error else "completed",
            "passed": result.passed_tasks if result else 0,
            "failed": result.failed_tasks if result else 0,
            "pass_rate": result.pass_rate if result else 0.0,
            "error": error,
        }
        if self._db is None:
            run = _MEM_RUNS.get(self._tenant_id, {}).get(suite_id, {}).get(run_id)
            if run is not None:
                run.update(values, task_results=task_results,
                           finished_at=datetime.now(UTC).isoformat())
            return
        async with _scoped(self._db, self._tenant_id) as s:
            await s.execute(
                sa_text(
                    "UPDATE eval_suite_results SET status = :status, passed_tasks = :passed, "
                    " failed_tasks = :failed, pass_rate = :pass_rate, error = :error, "
                    " task_results = CAST(:task_results AS json), finished_at = now() "
                    "WHERE tenant_id = :tid AND id = :id"
                ),
                {**values, "task_results": json.dumps(task_results),
                 "tid": self._tenant_id, "id": run_id},
            )

    async def list_runs(self, suite_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Newest-first run history of one suite."""
        if self._db is None:
            runs = list(_MEM_RUNS.get(self._tenant_id, {}).get(suite_id, {}).values())
            runs.sort(key=lambda r: r["run_at"], reverse=True)
            return [dict(r) for r in runs[:limit]]
        async with _scoped(self._db, self._tenant_id) as s:
            rows = (
                await s.execute(
                    sa_text(
                        "SELECT run_id, CASE WHEN status = 'running' AND run_at < :stale "
                        " THEN 'abandoned' ELSE status END, total_tasks, passed_tasks, "
                        " failed_tasks, pass_rate, task_results, error, run_at, finished_at "
                        "FROM eval_suite_results WHERE tenant_id = :tid AND suite_id = :sid "
                        "ORDER BY run_at DESC, id LIMIT :limit"
                    ),
                    {"tid": self._tenant_id, "sid": suite_id, "limit": limit,
                     "stale": datetime.now(UTC) - STALE_RUN_AFTER},
                )
            ).all()
        return [
            {"run_id": r[0], "status": r[1], "total": r[2], "passed": r[3], "failed": r[4],
             "pass_rate": r[5], "task_results": _loads(r[6]) or [], "error": r[7],
             "run_at": _iso(r[8]), "finished_at": _iso(r[9])}
            for r in rows
        ]
