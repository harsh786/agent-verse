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

    from app.intelligence.eval_suite import EvalSuiteResult, GoldenTask, GoldenTaskResult

__all__ = ["STALE_RUN_AFTER", "EvalSuiteStore", "task_from_dict", "task_to_dict"]

# Kept for importers; the live threshold is Settings.eval_suite_stalled_after_seconds.
STALE_RUN_AFTER = timedelta(hours=1)
# Per-task results kept on the run row (failures first); page the rest.
SUMMARY_TASKS = 200
# GET /eval-suites/{id} inlines at most this many tasks; page the rest.
GET_TASK_LIMIT = 200
MAX_TASK_PAGE = 500

_TASK_COLUMNS = (
    "task_id, goal, expected_phrases, expected_tool_calls, forbidden_tools, "
    "expected_output, min_score, max_iterations, tags, expected_citations, "
    "source_goal_id, valid_from"
)

_MEM_SUITES: dict[str, dict[str, dict[str, Any]]] = {}
_MEM_RUNS: dict[str, dict[str, dict[str, Any]]] = {}
_MEM_TASKS: dict[tuple[str, str], list[dict[str, Any]]] = {}


def _stalled_after_seconds() -> float:
    from app.core.config import get_settings

    return float(getattr(get_settings(), "eval_suite_stalled_after_seconds", 1800))


def _expired(row: dict[str, Any], now: datetime) -> bool:
    return row.get("lease_expires_at") is not None and row["lease_expires_at"] < now


_LAST_RUN_KEYS = ("run_id", "status", "total", "passed", "failed", "pass_rate", "run_at",
                  "finished_at", "dataset_version", "agent_id", "progress")


def _last_run_public(run: dict[str, Any]) -> dict[str, Any]:
    return {k: run[k] for k in _LAST_RUN_KEYS if k in run}


def _run_task_public(row: dict[str, Any]) -> dict[str, Any]:
    task = row.get("task") or {}
    return {
        "task_id": row["task_id"],
        "goal": task.get("goal", ""),
        "state": row.get("state"),
        "attempts": row.get("attempts", 0),
        "goal_id": row.get("goal_id"),
        "status": row.get("status"),
        "passed": bool(row.get("passed")),
        "score": row.get("score"),
        "terminal_event": row.get("terminal_event"),
        "failure_reasons": list(row.get("failure_reasons") or []),
        "judge": row.get("judge"),
        "duration_seconds": row.get("duration_seconds"),
    }


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
        "expected_citations": list(task.expected_citations),
        "source_goal_id": task.source_goal_id or "",
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
        expected_citations=list(data.get("expected_citations") or []),
        source_goal_id=str(data.get("source_goal_id") or "") or None,
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
        "max_iterations": 15, "tags": [], "expected_citations": [], "source_goal_id": "",
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
        "expected_citations": list(_loads(r[9]) or []),
        "source_goal_id": r[10] or "",
        "revision": int(r[11]),
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
        """The tenant's suites, each with its latest run (``last_run``, ``None`` when it
        never ran); a running one carries live ``progress``."""
        if self._db is None:
            out = []
            for v in _MEM_SUITES.get(self._tenant_id, {}).values():
                item = self._summary(v)
                runs = await self.list_runs(item["suite_id"], limit=1)
                item["last_run"] = _last_run_public(runs[0]) if runs else None
                out.append(item)
            return out
        stale = datetime.now(UTC) - timedelta(seconds=_stalled_after_seconds())
        async with _scoped(self._db, self._tenant_id) as s:
            rows = (
                await s.execute(
                    sa_text(
                        "SELECT s.id, s.name, s.description, s.dataset_version, s.created_at, "
                        " (SELECT count(*) FROM golden_tasks g WHERE g.tenant_id = s.tenant_id "
                        "   AND g.eval_suite_id = s.id AND g.valid_to IS NULL) AS n, "
                        " r.id, CASE WHEN r.status = 'running' "
                        "  AND COALESCE(r.last_progress_at, r.run_at) < :stale "
                        "  THEN 'abandoned' ELSE r.status END, r.total_tasks, r.passed_tasks, "
                        " r.failed_tasks, r.pass_rate, r.run_at, r.finished_at, "
                        " r.dataset_version, r.agent_id, r.status "
                        "FROM eval_suites s "
                        "LEFT JOIN LATERAL (SELECT * FROM eval_suite_results x "
                        "  WHERE x.tenant_id = s.tenant_id AND x.suite_id = s.id "
                        "  ORDER BY x.run_at DESC, x.id LIMIT 1) r ON true "
                        "WHERE s.tenant_id = :tid "
                        "ORDER BY s.created_at DESC, s.id LIMIT 500"
                    ),
                    {"tid": self._tenant_id, "stale": stale},
                )
            ).all()
        out = []
        for r in rows:
            last: dict[str, Any] | None = None
            if r[6] is not None:
                last = {
                    "run_id": r[6], "status": r[7], "total": r[8], "passed": r[9],
                    "failed": r[10], "pass_rate": r[11], "run_at": _iso(r[12]),
                    "finished_at": _iso(r[13]), "dataset_version": r[14], "agent_id": r[15],
                }
                if r[16] == "running":
                    last["progress"] = await self.run_progress(str(r[6]))
            out.append({
                "suite_id": r[0], "name": r[1], "description": r[2] or "",
                "dataset_version": int(r[3] or 0), "created_at": _iso(r[4]),
                "task_count": int(r[5]), "last_run": last,
            })
        return out

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
                " min_score, max_iterations, tags, expected_citations, source_goal_id, "
                " valid_from, position) VALUES "
                "(:id, :tid, :sid, :task_id, :goal, CAST(:phrases AS jsonb), "
                " CAST(:tools AS jsonb), CAST(:forbidden AS jsonb), :expected_output, "
                " :min_score, :max_iterations, CAST(:tags AS jsonb), "
                " CAST(:citations AS jsonb), :source_goal, :v, :pos)"
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
                    "tags": json.dumps(task["tags"]),
                    "citations": json.dumps(task.get("expected_citations") or []),
                    "source_goal": (task.get("source_goal_id") or None),
                    "v": version, "pos": position,
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
        self,
        suite_id: str,
        run_id: str,
        total: int = 0,
        *,
        dataset_version: int | None = None,
        agent_id: str | None = None,
        agent_config_hash: str | None = None,
        agent_version: int | None = None,
        enqueue: bool = False,
        tenant_plan: str | None = None,
        concurrency: int | None = None,
    ) -> int:
        """Record a ``running`` run; returns its task count.

        ``enqueue`` (MEM-53) also writes one ``pending`` result row per task of
        ``dataset_version`` (``INSERT ... SELECT``, so thousands of tasks cost one
        statement) for the durable workers to claim; ``total`` is then the
        number of rows enqueued.
        """
        now = datetime.now(UTC)
        if self._db is None:
            rows: list[dict[str, Any]] = []
            if enqueue and dataset_version is not None:
                suite = _MEM_SUITES.get(self._tenant_id, {}).get(suite_id) or {}
                for i, t in enumerate(_mem_tasks_at(suite, dataset_version), start=1):
                    task = {k: v for k, v in t.items() if k != "revision"}
                    rows.append({
                        "task_id": t["task_id"], "ordinal": i, "task": task,
                        "state": "pending", "attempts": 0, "lease_owner": None,
                        "lease_expires_at": None, "goal_id": None, "status": None,
                        "deadline_at": None, "next_check_at": None,
                        "passed": None, "score": None, "terminal_event": None,
                        "failure_reasons": [], "judge": None, "duration_seconds": None,
                    })
                total = len(rows)
                _MEM_TASKS[(self._tenant_id, run_id)] = rows
            _MEM_RUNS.setdefault(self._tenant_id, {}).setdefault(suite_id, {})[run_id] = {
                "run_id": run_id, "suite_id": suite_id, "status": "running", "total": total,
                "passed": 0, "failed": 0, "pass_rate": 0.0, "task_results": [], "error": None,
                "run_at": now.isoformat(), "finished_at": None,
                "dataset_version": dataset_version, "agent_id": agent_id,
                "agent_config_hash": agent_config_hash, "agent_version": agent_version,
                "tenant_plan": tenant_plan, "concurrency": concurrency,
                "last_progress_at": now.isoformat(), "unscored": 0,
            }
            return total
        async with _scoped(self._db, self._tenant_id) as s:
            await s.execute(
                sa_text(
                    "INSERT INTO eval_suite_results (id, suite_id, tenant_id, run_id, "
                    " total_tasks, status, dataset_version, agent_id, agent_config_hash, "
                    " agent_version, tenant_plan, concurrency, last_progress_at) "
                    "VALUES (:id, :sid, :tid, :id, :total, 'running', :dv, :aid, :ahash, "
                    " :aver, :plan, :conc, now())"
                ),
                {"id": run_id, "sid": suite_id, "tid": self._tenant_id, "total": total,
                 "dv": dataset_version, "aid": agent_id, "ahash": agent_config_hash,
                 "aver": agent_version, "plan": tenant_plan, "conc": concurrency},
            )
            if enqueue and dataset_version is not None:
                await s.execute(
                    sa_text(
                        "INSERT INTO eval_suite_task_results "
                        " (tenant_id, run_id, task_id, suite_id, ordinal, task) "
                        "SELECT CAST(:tid AS text), CAST(:rid AS text), task_id, "
                        " CAST(:sid AS text), "
                        " row_number() OVER (ORDER BY position, task_id), "
                        " jsonb_build_object('task_id', task_id, 'goal', goal, "
                        "  'expected_tools', expected_tool_calls, "
                        "  'forbidden_tools', forbidden_tools, "
                        "  'expected_output_contains', expected_phrases, "
                        "  'expected_output', expected_output, 'min_score', min_score, "
                        "  'max_iterations', max_iterations, 'tags', tags) "
                        "FROM golden_tasks WHERE tenant_id = CAST(:tid AS text) "
                        "AND eval_suite_id = CAST(:sid AS text) "
                        "AND valid_from <= :v AND (valid_to IS NULL OR valid_to > :v)"
                    ),
                    {"tid": self._tenant_id, "rid": run_id, "sid": suite_id,
                     "v": int(dataset_version)},
                )
                total = int(
                    (
                        await s.execute(
                            sa_text(
                                "UPDATE eval_suite_results SET total_tasks = ("
                                " SELECT count(*) FROM eval_suite_task_results "
                                " WHERE tenant_id = :tid AND run_id = :rid) "
                                "WHERE tenant_id = :tid AND id = :rid RETURNING total_tasks"
                            ),
                            {"tid": self._tenant_id, "rid": run_id},
                        )
                    ).scalar()
                    or 0
                )
        return total

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        """One run's binding and status (without per-task results)."""
        if self._db is None:
            for runs in _MEM_RUNS.get(self._tenant_id, {}).values():
                if run_id in runs:
                    return dict(runs[run_id])
            return None
        async with _scoped(self._db, self._tenant_id) as s:
            r = (
                await s.execute(
                    sa_text(
                        "SELECT run_id, suite_id, status, total_tasks, dataset_version, "
                        " agent_id, agent_config_hash, agent_version, tenant_plan, "
                        " concurrency, run_at, last_progress_at "
                        "FROM eval_suite_results WHERE tenant_id = :tid AND id = :rid"
                    ),
                    {"tid": self._tenant_id, "rid": run_id},
                )
            ).first()
        if r is None:
            return None
        return {"run_id": r[0], "suite_id": r[1], "status": r[2], "total": r[3],
                "dataset_version": r[4], "agent_id": r[5], "agent_config_hash": r[6],
                "agent_version": r[7], "tenant_plan": r[8], "concurrency": r[9],
                "run_at": _iso(r[10]), "last_progress_at": _iso(r[11])}

    async def claim_pending(
        self, run_id: str, owner: str, lease_seconds: float
    ) -> dict[str, Any] | None:
        """Lease the next task to submit: ``pending``, or ``submitting`` whose step died
        (expired lease). ``None`` when there is none."""
        if self._db is None:
            now = datetime.now(UTC)
            for row in _MEM_TASKS.get((self._tenant_id, run_id), []):
                expired = row["state"] == "submitting" and _expired(row, now)
                if row["state"] == "pending" or expired:
                    row.update(state="submitting", attempts=row["attempts"] + 1,
                               lease_owner=owner,
                               lease_expires_at=now + timedelta(seconds=lease_seconds))
                    self._mem_touch(run_id)
                    return {"task_id": row["task_id"], "task": dict(row["task"]),
                            "goal_id": row["goal_id"], "attempts": row["attempts"]}
            return None
        async with _scoped(self._db, self._tenant_id) as s:
            r = (
                await s.execute(
                    sa_text(
                        # MATERIALIZED: as a plain FROM/IN subquery the planner may
                        # rescan it per joined row, and every rescan SKIP LOCKs a
                        # different row, so one claim leased many tasks.
                        "WITH c AS MATERIALIZED (SELECT task_id FROM eval_suite_task_results "
                        "      WHERE tenant_id = :tid AND run_id = :rid AND (state = 'pending' "
                        "        OR (state = 'submitting' AND lease_expires_at < now())) "
                        "      ORDER BY ordinal LIMIT 1 FOR UPDATE SKIP LOCKED) "
                        "UPDATE eval_suite_task_results t SET state = 'submitting', "
                        " attempts = t.attempts + 1, lease_owner = :owner, "
                        " lease_expires_at = now() + make_interval(secs => :lease), "
                        " started_at = COALESCE(t.started_at, now()) "
                        "FROM c "
                        "WHERE t.tenant_id = :tid AND t.run_id = :rid AND t.task_id = c.task_id "
                        "RETURNING t.task_id, t.task, t.goal_id, t.attempts"
                    ),
                    {"tid": self._tenant_id, "rid": run_id, "owner": owner,
                     "lease": float(lease_seconds)},
                )
            ).first()
            if r is not None:
                await self._touch(s, run_id)
        if r is None:
            return None
        return {"task_id": r[0], "task": dict(_loads(r[1]) or {}), "goal_id": r[2],
                "attempts": int(r[3])}

    async def mark_waiting(
        self, run_id: str, task_id: str, owner: str, goal_id: str, deadline_seconds: float
    ) -> bool:
        """The task's goal was submitted: record it (before anyone waits on it) with
        its deadline. False when the lease was lost."""
        if self._db is None:
            row = self._mem_row(run_id, task_id)
            if row is None or row["lease_owner"] != owner or row["state"] != "submitting":
                return False
            now = datetime.now(UTC)
            row.update(state="waiting", goal_id=goal_id, lease_owner=None,
                       lease_expires_at=None, next_check_at=now,
                       deadline_at=now + timedelta(seconds=deadline_seconds))
            self._mem_touch(run_id)
            return True
        async with _scoped(self._db, self._tenant_id) as s:
            res = await s.execute(
                sa_text(
                    "UPDATE eval_suite_task_results SET state = 'waiting', goal_id = :gid, "
                    " lease_owner = NULL, lease_expires_at = NULL, next_check_at = now(), "
                    " deadline_at = now() + make_interval(secs => :deadline) "
                    "WHERE tenant_id = :tid AND run_id = :rid AND task_id = :task "
                    "AND lease_owner = :owner AND state = 'submitting'"
                ),
                {"gid": goal_id, "deadline": float(deadline_seconds), "tid": self._tenant_id,
                 "rid": run_id, "task": task_id, "owner": owner},
            )
            ok = bool(getattr(res, "rowcount", 0))
            if ok:
                await self._touch(s, run_id)
        return ok

    async def claim_due(
        self, run_id: str, owner: str, lease_seconds: float, limit: int
    ) -> list[dict[str, Any]]:
        """Lease ``waiting`` tasks whose goal is due for a status check."""
        if self._db is None:
            now = datetime.now(UTC)
            out: list[dict[str, Any]] = []
            for row in _MEM_TASKS.get((self._tenant_id, run_id), []):
                if len(out) >= limit:
                    break
                free = row["lease_owner"] is None or _expired(row, now)
                if row["state"] == "waiting" and row["next_check_at"] <= now and free:
                    row.update(lease_owner=owner,
                               lease_expires_at=now + timedelta(seconds=lease_seconds))
                    out.append({"task_id": row["task_id"], "task": dict(row["task"]),
                                "goal_id": row["goal_id"], "attempts": row["attempts"],
                                "overdue": row["deadline_at"] <= now})
            return out
        async with _scoped(self._db, self._tenant_id) as s:
            rows = (
                await s.execute(
                    sa_text(
                        "WITH c AS MATERIALIZED (SELECT task_id FROM eval_suite_task_results "
                        "      WHERE tenant_id = :tid AND run_id = :rid AND state = 'waiting' "
                        "        AND next_check_at <= now() "
                        "        AND (lease_owner IS NULL OR lease_expires_at < now()) "
                        "      ORDER BY next_check_at LIMIT :lim FOR UPDATE SKIP LOCKED) "
                        "UPDATE eval_suite_task_results t SET lease_owner = :owner, "
                        " lease_expires_at = now() + make_interval(secs => :lease) "
                        "FROM c "
                        "WHERE t.tenant_id = :tid AND t.run_id = :rid AND t.task_id = c.task_id "
                        "RETURNING t.task_id, t.task, t.goal_id, t.attempts, "
                        " t.deadline_at <= now()"
                    ),
                    {"tid": self._tenant_id, "rid": run_id, "owner": owner,
                     "lease": float(lease_seconds), "lim": int(limit)},
                )
            ).all()
        return [{"task_id": r[0], "task": dict(_loads(r[1]) or {}), "goal_id": r[2],
                 "attempts": int(r[3]), "overdue": bool(r[4])} for r in rows]

    async def defer_check(self, run_id: str, task_id: str, owner: str, seconds: float) -> bool:
        """The goal is still running: check it again in ``seconds`` (lease released)."""
        if self._db is None:
            row = self._mem_row(run_id, task_id)
            if row is None or row["lease_owner"] != owner or row["state"] != "waiting":
                return False
            row.update(lease_owner=None, lease_expires_at=None,
                       next_check_at=datetime.now(UTC) + timedelta(seconds=seconds))
            self._mem_touch(run_id)
            return True
        async with _scoped(self._db, self._tenant_id) as s:
            res = await s.execute(
                sa_text(
                    "UPDATE eval_suite_task_results SET lease_owner = NULL, "
                    " lease_expires_at = NULL, "
                    " next_check_at = now() + make_interval(secs => :secs) "
                    "WHERE tenant_id = :tid AND run_id = :rid AND task_id = :task "
                    "AND lease_owner = :owner AND state = 'waiting'"
                ),
                {"secs": float(seconds), "tid": self._tenant_id, "rid": run_id,
                 "task": task_id, "owner": owner},
            )
            ok = bool(getattr(res, "rowcount", 0))
            if ok:
                await self._touch(s, run_id)
        return ok

    async def count_inflight(self, run_id: str) -> int:
        """Tasks whose golden goal is being submitted or is running.

        A ``submitting`` row whose lease expired belongs to a dead step and is
        re-claimable, so it does not hold a slot (with concurrency 1 it would
        otherwise block the very claim that recovers it)."""
        if self._db is None:
            now = datetime.now(UTC)
            return sum(
                1 for r in _MEM_TASKS.get((self._tenant_id, run_id), [])
                if r["state"] == "waiting"
                or (r["state"] == "submitting" and not _expired(r, now))
            )
        async with _scoped(self._db, self._tenant_id) as s:
            n = (
                await s.execute(
                    sa_text(
                        "SELECT count(*) FROM eval_suite_task_results WHERE tenant_id = :tid "
                        "AND run_id = :rid AND (state = 'waiting' OR (state = 'submitting' "
                        " AND lease_expires_at >= now()))"
                    ),
                    {"tid": self._tenant_id, "rid": run_id},
                )
            ).scalar()
        return int(n or 0)

    async def heartbeat(self, run_id: str) -> None:
        """A worker step ran for this run (keeps it from looking stalled)."""
        if self._db is None:
            self._mem_touch(run_id)
            return
        async with _scoped(self._db, self._tenant_id) as s:
            await self._touch(s, run_id)

    async def record_task_result(
        self, run_id: str, owner: str, result: GoldenTaskResult
    ) -> bool:
        """Write a task's outcome (fenced on the lease). False: the lease was lost."""
        values = {
            "status": result.status, "passed": bool(result.passed), "score": result.score,
            "terminal_event": result.terminal_event, "goal_id": result.goal_id,
            "failure_reasons": list(result.failure_reasons), "judge": result.judge,
            "duration_seconds": round(float(result.duration_seconds), 3),
        }
        if self._db is None:
            row = self._mem_row(run_id, result.task_id)
            if (
                row is None or row["lease_owner"] != owner
                or row["state"] not in ("submitting", "waiting")
            ):
                return False
            keep_goal = row.get("goal_id")
            row.update(values, state="done", lease_owner=None, lease_expires_at=None)
            if result.goal_id is None:
                row["goal_id"] = keep_goal
            self._mem_touch(run_id)
            return True
        async with _scoped(self._db, self._tenant_id) as s:
            res = await s.execute(
                sa_text(
                    "UPDATE eval_suite_task_results SET state = 'done', status = :status, "
                    " passed = :passed, score = :score, terminal_event = :terminal_event, "
                    " goal_id = COALESCE(:goal_id, goal_id), "
                    " failure_reasons = CAST(:failure_reasons AS jsonb), "
                    " judge = CAST(:judge AS jsonb), duration_seconds = :duration_seconds, "
                    " lease_owner = NULL, lease_expires_at = NULL, finished_at = now() "
                    "WHERE tenant_id = :tid AND run_id = :rid AND task_id = :task "
                    "AND lease_owner = :owner AND state IN ('submitting', 'waiting')"
                ),
                {**values, "failure_reasons": json.dumps(values["failure_reasons"]),
                 "judge": json.dumps(values["judge"]) if values["judge"] is not None else None,
                 "tid": self._tenant_id, "rid": run_id, "task": result.task_id,
                 "owner": owner},
            )
            ok = bool(getattr(res, "rowcount", 0))
            if ok:
                await self._touch(s, run_id)
        return ok

    async def run_progress(self, run_id: str) -> dict[str, int]:
        """``{total, done, passed, failed, unscored, running, pending}`` from the task rows."""
        if self._db is None:
            rows = _MEM_TASKS.get((self._tenant_id, run_id), [])
            done = [r for r in rows if r["state"] == "done"]
            return {
                "total": len(rows), "done": len(done),
                "passed": sum(1 for r in done if r["passed"]),
                "failed": sum(1 for r in done if not r["passed"]),
                "unscored": sum(1 for r in done if r["status"] != "scored"),
                "running": sum(1 for r in rows if r["state"] in ("submitting", "waiting")),
                "pending": sum(1 for r in rows if r["state"] == "pending"),
            }
        async with _scoped(self._db, self._tenant_id) as s:
            r = (
                await s.execute(
                    sa_text(
                        "SELECT count(*), count(*) FILTER (WHERE state = 'done'), "
                        " count(*) FILTER (WHERE state = 'done' AND passed), "
                        " count(*) FILTER (WHERE state = 'done' AND NOT passed), "
                        " count(*) FILTER (WHERE state = 'done' AND status <> 'scored'), "
                        " count(*) FILTER (WHERE state IN ('submitting', 'waiting')), "
                        " count(*) FILTER (WHERE state = 'pending') "
                        "FROM eval_suite_task_results WHERE tenant_id = :tid AND run_id = :rid"
                    ),
                    {"tid": self._tenant_id, "rid": run_id},
                )
            ).one()
        keys = ("total", "done", "passed", "failed", "unscored", "running", "pending")
        return {k: int(v or 0) for k, v in zip(keys, r, strict=True)}

    async def finalize_run(self, run_id: str) -> dict[str, Any] | None:
        """Complete the run once every task is done. Returns the completed run only
        to the ONE caller whose update completed it (post-run hooks run once)."""
        progress = await self.run_progress(run_id)
        if progress["done"] < progress["total"]:
            return None
        total = progress["total"]
        pass_rate = progress["passed"] / total if total else 0.0
        summary = await self.list_run_tasks(run_id, limit=SUMMARY_TASKS, failures_first=True)
        if self._db is None:
            for runs in _MEM_RUNS.get(self._tenant_id, {}).values():
                run = runs.get(run_id)
                if run is not None and run["status"] == "running":
                    run.update(status="completed", passed=progress["passed"],
                               failed=progress["failed"], unscored=progress["unscored"],
                               pass_rate=pass_rate, task_results=summary,
                               finished_at=datetime.now(UTC).isoformat())
                    return dict(run)
            return None
        async with _scoped(self._db, self._tenant_id) as s:
            r = (
                await s.execute(
                    sa_text(
                        "UPDATE eval_suite_results SET status = 'completed', "
                        " passed_tasks = :passed, failed_tasks = :failed, "
                        " pass_rate = :rate, total_tasks = :total, "
                        " task_results = CAST(:summary AS json), finished_at = now(), "
                        " last_progress_at = now() "
                        "WHERE tenant_id = :tid AND id = :rid AND status = 'running' "
                        "RETURNING run_id"
                    ),
                    {"passed": progress["passed"], "failed": progress["failed"],
                     "rate": pass_rate, "total": total, "summary": json.dumps(summary),
                     "tid": self._tenant_id, "rid": run_id},
                )
            ).first()
        if r is None:
            return None
        return await self.get_run(run_id)

    async def fail_run(self, run_id: str, error: str) -> None:
        """Mark a run that cannot continue (its suite was deleted, ...) as failed."""
        if self._db is None:
            for runs in _MEM_RUNS.get(self._tenant_id, {}).values():
                if run_id in runs and runs[run_id]["status"] == "running":
                    runs[run_id].update(status="failed", error=error[:2000],
                                        finished_at=datetime.now(UTC).isoformat())
            return
        async with _scoped(self._db, self._tenant_id) as s:
            await s.execute(
                sa_text(
                    "UPDATE eval_suite_results SET status = 'failed', error = :err, "
                    " finished_at = now() WHERE tenant_id = :tid AND id = :rid "
                    "AND status = 'running'"
                ),
                {"err": error[:2000], "tid": self._tenant_id, "rid": run_id},
            )

    async def list_run_tasks(
        self, run_id: str, *, limit: int = 100, offset: int = 0, failures_first: bool = False
    ) -> list[dict[str, Any]]:
        """Per-task results of a run (paged)."""
        limit = max(1, min(int(limit), MAX_TASK_PAGE))
        if self._db is None:
            rows = list(_MEM_TASKS.get((self._tenant_id, run_id), []))
            if failures_first:
                rows.sort(key=lambda r: (bool(r["passed"]), r["ordinal"]))
            return [_run_task_public(r) for r in rows[offset: offset + limit]]
        order = "passed IS TRUE, ordinal" if failures_first else "ordinal"
        async with _scoped(self._db, self._tenant_id) as s:
            rows2 = (
                await s.execute(
                    sa_text(
                        "SELECT task_id, ordinal, state, attempts, goal_id, status, passed, "
                        " score, terminal_event, failure_reasons, judge, duration_seconds, task "
                        "FROM eval_suite_task_results WHERE tenant_id = :tid AND run_id = :rid "
                        f"ORDER BY {order} LIMIT :lim OFFSET :off"
                    ),
                    {"tid": self._tenant_id, "rid": run_id, "lim": limit,
                     "off": max(0, int(offset))},
                )
            ).all()
        return [
            _run_task_public({
                "task_id": r[0], "ordinal": r[1], "state": r[2], "attempts": r[3],
                "goal_id": r[4], "status": r[5], "passed": r[6], "score": r[7],
                "terminal_event": r[8], "failure_reasons": _loads(r[9]) or [],
                "judge": _loads(r[10]), "duration_seconds": r[11], "task": _loads(r[12]) or {},
            })
            for r in rows2
        ]

    async def _touch(self, s: AsyncSession, run_id: str) -> None:
        await s.execute(
            sa_text(
                "UPDATE eval_suite_results SET last_progress_at = now() "
                "WHERE tenant_id = :tid AND id = :rid"
            ),
            {"tid": self._tenant_id, "rid": run_id},
        )

    def _mem_touch(self, run_id: str) -> None:
        for runs in _MEM_RUNS.get(self._tenant_id, {}).values():
            if run_id in runs:
                runs[run_id]["last_progress_at"] = datetime.now(UTC).isoformat()

    def _mem_row(self, run_id: str, task_id: str) -> dict[str, Any] | None:
        return next(
            (r for r in _MEM_TASKS.get((self._tenant_id, run_id), [])
             if r["task_id"] == task_id),
            None,
        )

    async def finish_run(
        self,
        suite_id: str,
        run_id: str,
        *,
        result: EvalSuiteResult | None,
        error: str | None = None,
    ) -> None:
        """Record an in-process (library) run's outcome in one write."""
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

    async def list_runs(
        self, suite_id: str, *, limit: int = 50, agent_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Newest-first run history of one suite (optionally: runs against one agent).

        A running run carries live ``progress`` from its task rows; it is reported
        ``abandoned`` only when no worker has made progress for
        ``eval_suite_stalled_after_seconds`` (heartbeat), never because it is long.
        """
        stale_after = timedelta(seconds=_stalled_after_seconds())
        if self._db is None:
            runs = list(_MEM_RUNS.get(self._tenant_id, {}).get(suite_id, {}).values())
            if agent_id is not None:
                runs = [r for r in runs if r.get("agent_id") == agent_id]
            runs.sort(key=lambda r: r["run_at"], reverse=True)
            out = []
            for r in runs[:limit]:
                item = dict(r)
                if item["status"] == "running":
                    item["progress"] = await self.run_progress(item["run_id"])
                    last = item.get("last_progress_at") or item["run_at"]
                    if datetime.fromisoformat(last) < datetime.now(UTC) - stale_after:
                        item["status"] = "abandoned"
                out.append(item)
            return out
        agent_filter = "AND agent_id = :aid " if agent_id is not None else ""
        async with _scoped(self._db, self._tenant_id) as s:
            rows = (
                await s.execute(
                    sa_text(
                        "SELECT run_id, CASE WHEN status = 'running' "
                        " AND COALESCE(last_progress_at, run_at) < :stale "
                        " THEN 'abandoned' ELSE status END, total_tasks, passed_tasks, "
                        " failed_tasks, pass_rate, task_results, error, run_at, finished_at, "
                        " dataset_version, agent_id, agent_config_hash, agent_version, "
                        " last_progress_at, status "
                        "FROM eval_suite_results WHERE tenant_id = :tid AND suite_id = :sid "
                        f"{agent_filter}ORDER BY run_at DESC, id LIMIT :limit"
                    ),
                    {"tid": self._tenant_id, "sid": suite_id, "limit": limit,
                     "stale": datetime.now(UTC) - stale_after, "aid": agent_id},
                )
            ).all()
        out = []
        for r in rows:
            item = {
                "run_id": r[0], "status": r[1], "total": r[2], "passed": r[3], "failed": r[4],
                "pass_rate": r[5], "task_results": _loads(r[6]) or [], "error": r[7],
                "run_at": _iso(r[8]), "finished_at": _iso(r[9]), "dataset_version": r[10],
                "agent_id": r[11], "agent_config_hash": r[12], "agent_version": r[13],
                "last_progress_at": _iso(r[14]),
            }
            if r[15] == "running":
                item["progress"] = await self.run_progress(str(r[0]))
                item["task_results"] = await self.list_run_tasks(
                    str(r[0]), limit=SUMMARY_TASKS, failures_first=True
                )
            out.append(item)
        return out
