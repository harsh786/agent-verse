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
explicit tenant predicate. Golden tasks are copy-on-write revision rows in
``golden_tasks`` (MEM-54): every add / edit / delete / import bumps the suite's
``dataset_version``, a revision is valid for ``[valid_from, valid_to)``, so any
past version can be read back exactly, and runs record the version they ran.
A run is recorded as ``running`` first and finished by a background task; a
``running`` row whose worker died is reported as ``abandoned`` once it is older
than :data:`STALE_RUN_AFTER`.

Without a database (unit tests / in-memory dev mode) the store falls back to
process-local dicts keyed by tenant — never by suite id alone.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable
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
# GET /eval-suites/{id} inlines at most this many tasks; page the rest.
GET_TASK_LIMIT = 200
MAX_TASK_PAGE = 500

_TASK_COLUMNS = (
    "task_id, goal, expected_phrases, expected_tool_calls, forbidden_tools, "
    "expected_output, min_score, max_iterations, tags, valid_from"
)

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


def _clean(task: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """A task dict with exactly the stored fields (unknown keys dropped).

    ``partial`` keeps only the keys present (an edit), else missing ones get
    their defaults.
    """
    out: dict[str, Any] = {}
    if "task_id" in task:
        out["task_id"] = str(task["task_id"])
    defaults: dict[str, Any] = {
        "goal": "", "expected_tools": [], "forbidden_tools": [],
        "expected_output_contains": [], "expected_output": "", "min_score": 0.8,
        "max_iterations": 15, "tags": [],
    }
    for key, default in defaults.items():
        if partial and task.get(key) is None:
            continue
        value = task.get(key, default)
        if value is None:
            value = default
        if isinstance(default, list):
            value = [str(v) for v in value if str(v).strip()]
        elif key == "min_score":
            value = float(value)
        elif key == "max_iterations":
            value = int(value)
        else:
            value = str(value)
        out[key] = value
    return out


def _task_row(r: Any) -> dict[str, Any]:
    return {
        "task_id": r[0],
        "goal": r[1],
        "expected_output_contains": list(_loads(r[2]) or []),
        "expected_tools": list(_loads(r[3]) or []),
        "forbidden_tools": list(_loads(r[4]) or []),
        "expected_output": r[5] or "",
        "min_score": float(r[6]),
        "max_iterations": int(r[7]),
        "tags": list(_loads(r[8]) or []),
        "revision": int(r[9]),
    }


def _public(rev: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in rev.items() if k not in {"valid_from", "valid_to", "position"}}
    out["revision"] = int(rev.get("valid_from", 0))
    return out


def _mem_tasks_at(suite: dict[str, Any], version: int) -> list[dict[str, Any]]:
    revs = [
        r for r in suite.get("revisions", [])
        if int(r["valid_from"]) <= version
        and (r.get("valid_to") is None or int(r["valid_to"]) > version)
    ]
    revs.sort(key=lambda r: (int(r.get("position", 0)), r["task_id"]))
    return [_public(r) for r in revs]


def _mem_current(suite: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    return next(
        (r for r in suite.get("revisions", [])
         if r["task_id"] == task_id and r.get("valid_to") is None),
        None,
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
    """One tenant's eval suites, their versioned golden datasets and run history."""

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
                "revisions": [], "dataset_version": 0, "created_at": now.isoformat(),
            }
            return self._summary(suites[suite_id])
        async with _scoped(self._db, self._tenant_id) as s:
            row = (
                await s.execute(
                    sa_text(
                        "INSERT INTO eval_suites (id, tenant_id, name, description) "
                        "VALUES (:id, :tid, :name, :description) "
                        "ON CONFLICT (tenant_id, id) DO NOTHING RETURNING created_at"
                    ),
                    {"id": suite_id, "tid": self._tenant_id, "name": name,
                     "description": description},
                )
            ).first()
        if row is None:
            return None
        return {"suite_id": suite_id, "name": name, "description": description,
                "task_count": 0, "dataset_version": 0, "created_at": _iso(row[0])}

    @staticmethod
    def _summary(suite: dict[str, Any]) -> dict[str, Any]:
        return {
            "suite_id": suite["suite_id"],
            "name": suite["name"],
            "description": suite.get("description", ""),
            "task_count": len(_mem_tasks_at(suite, int(suite.get("dataset_version", 0)))),
            "dataset_version": int(suite.get("dataset_version", 0)),
            "created_at": suite.get("created_at", ""),
        }

    async def list(self) -> list[dict[str, Any]]:
        if self._db is None:
            return [self._summary(v) for v in _MEM_SUITES.get(self._tenant_id, {}).values()]
        async with _scoped(self._db, self._tenant_id) as s:
            rows = (
                await s.execute(
                    sa_text(
                        "SELECT s.id, s.name, s.description, s.dataset_version, s.created_at, "
                        " (SELECT count(*) FROM golden_tasks g WHERE g.tenant_id = s.tenant_id "
                        "   AND g.eval_suite_id = s.id AND g.valid_to IS NULL) AS n "
                        "FROM eval_suites s WHERE s.tenant_id = :tid "
                        "ORDER BY s.created_at DESC, s.id LIMIT 500"
                    ),
                    {"tid": self._tenant_id},
                )
            ).all()
        return [
            {"suite_id": r[0], "name": r[1], "description": r[2] or "",
             "dataset_version": int(r[3] or 0), "created_at": _iso(r[4]), "task_count": int(r[5])}
            for r in rows
        ]

    async def get(
        self, suite_id: str, *, task_limit: int = GET_TASK_LIMIT
    ) -> dict[str, Any] | None:
        """Suite metadata plus the first ``task_limit`` current tasks, or ``None``.

        ``tasks_truncated`` says when the dataset has more; page the rest with
        :meth:`list_tasks`.
        """
        meta = await self.get_meta(suite_id)
        if meta is None:
            return None
        version = int(meta["dataset_version"])
        tasks = await self.list_tasks(suite_id, version=version, limit=task_limit)
        return {**meta, "tasks": tasks, "tasks_truncated": meta["task_count"] > len(tasks)}

    async def get_meta(self, suite_id: str) -> dict[str, Any] | None:
        """Suite metadata (with the current version's task count), or ``None``."""
        if self._db is None:
            suite = _MEM_SUITES.get(self._tenant_id, {}).get(suite_id)
            return None if suite is None else self._summary(suite)
        async with _scoped(self._db, self._tenant_id) as s:
            row = (
                await s.execute(
                    sa_text(
                        "SELECT s.id, s.name, s.description, s.dataset_version, s.created_at, "
                        " (SELECT count(*) FROM golden_tasks g WHERE g.tenant_id = s.tenant_id "
                        "   AND g.eval_suite_id = s.id AND g.valid_to IS NULL) "
                        "FROM eval_suites s WHERE s.tenant_id = :tid AND s.id = :id"
                    ),
                    {"tid": self._tenant_id, "id": suite_id},
                )
            ).first()
        if row is None:
            return None
        return {"suite_id": row[0], "name": row[1], "description": row[2] or "",
                "dataset_version": int(row[3] or 0), "created_at": _iso(row[4]),
                "task_count": int(row[5])}

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
            await s.execute(
                sa_text("DELETE FROM golden_tasks WHERE tenant_id = :tid AND eval_suite_id = :id"),
                {"tid": self._tenant_id, "id": suite_id},
            )
        return bool(getattr(result, "rowcount", 0))

    # ── versioned golden dataset (MEM-54) ─────────────────────────────────────

    async def list_tasks(
        self,
        suite_id: str,
        *,
        version: int | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """The tasks of dataset ``version`` (default: current), in position order, paged."""
        limit = max(1, min(int(limit), MAX_TASK_PAGE))
        offset = max(0, int(offset))
        if self._db is None:
            suite = _MEM_SUITES.get(self._tenant_id, {}).get(suite_id)
            if suite is None:
                return []
            v = int(suite["dataset_version"]) if version is None else int(version)
            return [dict(t) for t in _mem_tasks_at(suite, v)[offset: offset + limit]]
        async with _scoped(self._db, self._tenant_id) as s:
            v = version
            if v is None:
                v = (
                    await s.execute(
                        sa_text(
                            "SELECT dataset_version FROM eval_suites "
                            "WHERE tenant_id = :tid AND id = :id"
                        ),
                        {"tid": self._tenant_id, "id": suite_id},
                    )
                ).scalar()
                if v is None:
                    return []
            rows = (
                await s.execute(
                    sa_text(
                        f"SELECT {_TASK_COLUMNS} FROM golden_tasks "
                        "WHERE tenant_id = :tid AND eval_suite_id = :sid "
                        "AND valid_from <= :v AND (valid_to IS NULL OR valid_to > :v) "
                        "ORDER BY position, task_id LIMIT :lim OFFSET :off"
                    ),
                    {"tid": self._tenant_id, "sid": suite_id, "v": int(v),
                     "lim": limit, "off": offset},
                )
            ).all()
        return [_task_row(r) for r in rows]

    async def iter_tasks(self, suite_id: str, version: int) -> AsyncIterator[dict[str, Any]]:
        """Every task of dataset ``version``, page by page (exports, run fan-out)."""
        offset = 0
        while True:
            page = await self.list_tasks(
                suite_id, version=version, limit=MAX_TASK_PAGE, offset=offset
            )
            for task in page:
                yield task
            if len(page) < MAX_TASK_PAGE:
                return
            offset += len(page)

    async def add_task(self, suite_id: str, task: dict[str, Any]) -> int | None:
        """Add a golden task as a new dataset version; ``None`` when the suite is not this tenant's.

        Returns the new dataset version.
        """
        result = await self.import_tasks(suite_id, [task], replace=False)
        return None if result is None else result["dataset_version"]

    async def update_task(
        self,
        suite_id: str,
        task_id: str,
        changes: dict[str, Any],
        *,
        validate: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any] | None:
        """Edit a task: close its current revision, insert the edited one (new version).

        Returns ``{"dataset_version", "task"}``, or ``None`` when the suite or the
        task (in the current version) does not exist.
        """
        if self._db is None:
            suite = _MEM_SUITES.get(self._tenant_id, {}).get(suite_id)
            if suite is None:
                return None
            current = _mem_current(suite, task_id)
            if current is None:
                return None
            v = int(suite["dataset_version"]) + 1
            edited = {**current, **_clean(changes, partial=True), "task_id": task_id,
                      "valid_from": v, "valid_to": None}
            if validate is not None:
                validate(edited)
            current["valid_to"] = v
            suite["revisions"].append(edited)
            suite["dataset_version"] = v
            return {"dataset_version": v, "task": _public(edited)}
        async with _scoped(self._db, self._tenant_id) as s:
            current_row = (
                await s.execute(
                    sa_text(
                        f"SELECT {_TASK_COLUMNS}, position FROM golden_tasks "
                        "WHERE tenant_id = :tid AND eval_suite_id = :sid AND task_id = :task "
                        "AND valid_to IS NULL FOR UPDATE"
                    ),
                    {"tid": self._tenant_id, "sid": suite_id, "task": task_id},
                )
            ).first()
            if current_row is None:
                return None
            edited = {**_task_row(current_row), **_clean(changes, partial=True),
                      "task_id": task_id}
            if validate is not None:
                validate(edited)
            v = await self._bump_version(s, suite_id)
            if v is None:
                return None
            await s.execute(
                sa_text(
                    "UPDATE golden_tasks SET valid_to = :v WHERE tenant_id = :tid "
                    "AND eval_suite_id = :sid AND task_id = :task AND valid_to IS NULL"
                ),
                {"v": v, "tid": self._tenant_id, "sid": suite_id, "task": task_id},
            )
            await self._insert_revisions(
                s, suite_id, [(edited, int(current_row[-1]))], version=v
            )
        edited["revision"] = v
        return {"dataset_version": v, "task": edited}

    async def delete_task(self, suite_id: str, task_id: str) -> int | None:
        """Remove a task from the dataset (new version); ``None`` when not found."""
        if self._db is None:
            suite = _MEM_SUITES.get(self._tenant_id, {}).get(suite_id)
            current = None if suite is None else _mem_current(suite, task_id)
            if suite is None or current is None:
                return None
            v = int(suite["dataset_version"]) + 1
            current["valid_to"] = v
            suite["dataset_version"] = v
            return v
        async with _scoped(self._db, self._tenant_id) as s:
            exists = (
                await s.execute(
                    sa_text(
                        "SELECT 1 FROM golden_tasks WHERE tenant_id = :tid "
                        "AND eval_suite_id = :sid AND task_id = :task AND valid_to IS NULL "
                        "FOR UPDATE"
                    ),
                    {"tid": self._tenant_id, "sid": suite_id, "task": task_id},
                )
            ).first()
            if exists is None:
                return None
            v = await self._bump_version(s, suite_id)
            if v is None:
                return None
            await s.execute(
                sa_text(
                    "UPDATE golden_tasks SET valid_to = :v WHERE tenant_id = :tid "
                    "AND eval_suite_id = :sid AND task_id = :task AND valid_to IS NULL"
                ),
                {"v": v, "tid": self._tenant_id, "sid": suite_id, "task": task_id},
            )
        return v

    async def import_tasks(
        self, suite_id: str, tasks: list[dict[str, Any]], *, replace: bool
    ) -> dict[str, Any] | None:
        """Add (or, with ``replace``, swap in) many tasks as ONE new dataset version.

        Task ids are kept when given (a task id that is already current is a
        conflict: ``ValueError``), else generated. ``None`` when the suite is
        not this tenant's.
        """
        prepared = [_clean({**t, "task_id": t.get("task_id") or uuid.uuid4().hex}) for t in tasks]
        ids = [t["task_id"] for t in prepared]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate task_id in the import")
        if self._db is None:
            suite = _MEM_SUITES.get(self._tenant_id, {}).get(suite_id)
            if suite is None:
                return None
            v = int(suite["dataset_version"]) + 1
            current = _mem_tasks_at(suite, v - 1)
            if replace:
                for rev in suite["revisions"]:
                    if rev.get("valid_to") is None:
                        rev["valid_to"] = v
            elif {t["task_id"] for t in current} & set(ids):
                raise ValueError("a task with that task_id already exists")
            pos = max((int(r.get("position", 0)) for r in suite["revisions"]), default=0)
            for i, t in enumerate(prepared, start=1):
                suite["revisions"].append(
                    {**t, "valid_from": v, "valid_to": None, "position": pos + i}
                )
            suite["dataset_version"] = v
            return {"dataset_version": v, "task_ids": ids}
        async with _scoped(self._db, self._tenant_id) as s:
            v = await self._bump_version(s, suite_id)
            if v is None:
                return None
            if replace:
                await s.execute(
                    sa_text(
                        "UPDATE golden_tasks SET valid_to = :v WHERE tenant_id = :tid "
                        "AND eval_suite_id = :sid AND valid_to IS NULL"
                    ),
                    {"v": v, "tid": self._tenant_id, "sid": suite_id},
                )
            elif ids:
                clash = (
                    await s.execute(
                        sa_text(
                            "SELECT task_id FROM golden_tasks WHERE tenant_id = :tid "
                            "AND eval_suite_id = :sid AND valid_to IS NULL "
                            "AND task_id = ANY(:ids) LIMIT 1"
                        ),
                        {"tid": self._tenant_id, "sid": suite_id, "ids": ids},
                    )
                ).first()
                if clash is not None:
                    raise ValueError(f"a task with task_id {clash[0]!r} already exists")
            pos = int(
                (
                    await s.execute(
                        sa_text(
                            "SELECT COALESCE(max(position), 0) FROM golden_tasks "
                            "WHERE tenant_id = :tid AND eval_suite_id = :sid"
                        ),
                        {"tid": self._tenant_id, "sid": suite_id},
                    )
                ).scalar()
                or 0
            )
            await self._insert_revisions(
                s, suite_id, [(t, pos + i) for i, t in enumerate(prepared, start=1)], version=v
            )
        return {"dataset_version": v, "task_ids": ids}

    async def _bump_version(self, s: AsyncSession, suite_id: str) -> int | None:
        # The row lock serializes concurrent edits of one suite across replicas.
        v = (
            await s.execute(
                sa_text(
                    "UPDATE eval_suites SET dataset_version = dataset_version + 1, "
                    "updated_at = now() WHERE tenant_id = :tid AND id = :id "
                    "RETURNING dataset_version"
                ),
                {"tid": self._tenant_id, "id": suite_id},
            )
        ).scalar()
        return None if v is None else int(v)

    async def _insert_revisions(
        self,
        s: AsyncSession,
        suite_id: str,
        rows: list[tuple[dict[str, Any], int]],
        *,
        version: int,
    ) -> None:
        """Insert ``(task, position)`` revisions valid from ``version`` (batched executemany)."""
        if not rows:
            return
        await s.execute(
            sa_text(
                "INSERT INTO golden_tasks (id, tenant_id, eval_suite_id, task_id, goal, "
                " expected_phrases, expected_tool_calls, forbidden_tools, expected_output, "
                " min_score, max_iterations, tags, valid_from, position) VALUES "
                "(:id, :tid, :sid, :task_id, :goal, CAST(:phrases AS jsonb), "
                " CAST(:tools AS jsonb), CAST(:forbidden AS jsonb), :expected_output, "
                " :min_score, :max_iterations, CAST(:tags AS jsonb), :v, :pos)"
            ),
            [
                {
                    "id": uuid.uuid4().hex, "tid": self._tenant_id, "sid": suite_id,
                    "task_id": task["task_id"], "goal": task["goal"],
                    "phrases": json.dumps(task["expected_output_contains"]),
                    "tools": json.dumps(task["expected_tools"]),
                    "forbidden": json.dumps(task["forbidden_tools"]),
                    "expected_output": task["expected_output"],
                    "min_score": task["min_score"], "max_iterations": task["max_iterations"],
                    "tags": json.dumps(task["tags"]), "v": version, "pos": position,
                }
                for task, position in rows
            ],
        )

    async def export(self, suite_id: str, *, version: int | None = None) -> dict[str, Any] | None:
        """The whole dataset ``version`` (default: current) as a portable document."""
        meta = await self.get_meta(suite_id)
        if meta is None:
            return None
        v = int(meta["dataset_version"]) if version is None else int(version)
        if v < 0 or v > int(meta["dataset_version"]):
            raise LookupError(f"dataset version {v} does not exist")
        tasks = [t async for t in self.iter_tasks(suite_id, v)]
        return {
            "format": "agentverse.golden_dataset.v1",
            "suite_id": suite_id,
            "name": meta["name"],
            "description": meta["description"],
            "dataset_version": v,
            "task_count": len(tasks),
            "tasks": tasks,
        }

    # ── runs ──────────────────────────────────────────────────────────────────

    async def start_run(
        self, suite_id: str, run_id: str, total: int, *, dataset_version: int | None = None
    ) -> None:
        if self._db is None:
            _MEM_RUNS.setdefault(self._tenant_id, {}).setdefault(suite_id, {})[run_id] = {
                "run_id": run_id, "status": "running", "total": total, "passed": 0,
                "failed": 0, "pass_rate": 0.0, "task_results": [], "error": None,
                "run_at": datetime.now(UTC).isoformat(), "finished_at": None,
                "dataset_version": dataset_version,
            }
            return
        async with _scoped(self._db, self._tenant_id) as s:
            await s.execute(
                sa_text(
                    "INSERT INTO eval_suite_results (id, suite_id, tenant_id, run_id, "
                    " total_tasks, status, dataset_version) "
                    "VALUES (:id, :sid, :tid, :id, :total, 'running', :dv)"
                ),
                {"id": run_id, "sid": suite_id, "tid": self._tenant_id, "total": total,
                 "dv": dataset_version},
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
                        " failed_tasks, pass_rate, task_results, error, run_at, finished_at, "
                        " dataset_version "
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
             "run_at": _iso(r[8]), "finished_at": _iso(r[9]), "dataset_version": r[10]}
            for r in rows
        ]
