"""Durable storage for the AI-Ops centre (eval datasets, results, drift state).

`app/api/ai_ops.py` is a mounted, live router whose entire state — evaluation
datasets and their golden tasks, eval results, LLM-judge configs, metric
baselines and drift alerts — lived in module-level dicts. Everything was lost on
restart and invisible to every other replica, so a baseline set on one pod meant
drift was computed against "no baseline" on the next request served elsewhere.

Every method is tenant-scoped through ``sqlalchemy_rls_context``; the underlying
tables use ENABLE + FORCE row level security.
"""

from __future__ import annotations

import datetime
import json
from typing import Any

from sqlalchemy import text

from app.db.rls import sqlalchemy_rls_context


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return str(value)


class AIOpsStore:
    """Postgres-backed store for the ai_ops_* tables."""

    def __init__(self, db_factory: Any) -> None:
        self._db = db_factory

    # ── datasets (versioned, P7-3) ──────────────────────────────────────────
    async def create_dataset(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        name: str,
        description: str,
        golden_tasks: list[dict[str, Any]],
    ) -> None:
        """Create a dataset; its golden tasks are published version 1."""
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            params = {
                "id": dataset_id,
                "t": tenant_id,
                "n": name,
                "d": description,
                "g": json.dumps(golden_tasks),
            }
            await s.execute(
                text(
                    "INSERT INTO ai_ops_datasets "
                    "(id, tenant_id, name, description, golden_tasks, version) "
                    "VALUES (:id, :t, :n, :d, CAST(:g AS jsonb), 1)"
                ),
                params,
            )
            await s.execute(
                text(
                    "INSERT INTO ai_ops_dataset_versions "
                    "(tenant_id, dataset_id, version, status, golden_tasks, published_at) "
                    "VALUES (:t, :id, 1, 'published', CAST(:g AS jsonb), NOW())"
                ),
                params,
            )
            await s.commit()

    _HEAD_SQL = (
        "SELECT d.id, d.tenant_id, d.name, d.description, d.golden_tasks, d.version, "
        "       d.created_at, COALESCE(v.status, 'published'), "
        "       (SELECT max(p.version) FROM ai_ops_dataset_versions p "
        "         WHERE p.tenant_id = d.tenant_id AND p.dataset_id = d.id "
        "           AND p.status = 'published') "
        "FROM ai_ops_datasets d "
        "LEFT JOIN ai_ops_dataset_versions v "
        "  ON v.tenant_id = d.tenant_id AND v.dataset_id = d.id AND v.version = d.version "
    )

    async def get_dataset(self, tenant_id: str, dataset_id: str) -> dict[str, Any] | None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(self._HEAD_SQL + "WHERE d.tenant_id = :t AND d.id = :id"),
                    {"t": tenant_id, "id": dataset_id},
                )
            ).fetchone()
        return _dataset_row(row) if row is not None else None

    async def list_datasets(self, tenant_id: str, limit: int = 200) -> list[dict[str, Any]]:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            rows = (
                await s.execute(
                    text(
                        self._HEAD_SQL + "WHERE d.tenant_id = :t "
                        "ORDER BY d.created_at DESC LIMIT :k"
                    ),
                    {"t": tenant_id, "k": limit},
                )
            ).fetchall()
        return [_dataset_row(r) for r in rows]

    async def get_dataset_version(
        self, tenant_id: str, dataset_id: str, version: int
    ) -> dict[str, Any] | None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(
                        "SELECT v.version, v.status, v.golden_tasks, v.created_at, "
                        "       v.published_at, d.name "
                        "FROM ai_ops_dataset_versions v JOIN ai_ops_datasets d "
                        "  ON d.tenant_id = v.tenant_id AND d.id = v.dataset_id "
                        "WHERE v.tenant_id = :t AND v.dataset_id = :id AND v.version = :v"
                    ),
                    {"t": tenant_id, "id": dataset_id, "v": int(version)},
                )
            ).fetchone()
        if row is None:
            return None
        tasks = list(row[2] or [])
        return {
            "dataset_id": dataset_id,
            "name": row[5],
            "version": int(row[0]),
            "status": row[1],
            "golden_tasks": tasks,
            "task_count": len(tasks),
            "created_at": _iso(row[3]),
            "published_at": _iso(row[4]),
        }

    async def list_dataset_versions(
        self, tenant_id: str, dataset_id: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        from app.evals.dataset_versions import DatasetNotFoundError

        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            rows = (
                await s.execute(
                    text(
                        "SELECT version, status, jsonb_array_length(golden_tasks), "
                        "       created_at, published_at "
                        "FROM ai_ops_dataset_versions "
                        "WHERE tenant_id = :t AND dataset_id = :id "
                        "ORDER BY version DESC LIMIT :k"
                    ),
                    {"t": tenant_id, "id": dataset_id, "k": limit},
                )
            ).fetchall()
        if not rows:
            raise DatasetNotFoundError(dataset_id)
        return [
            {
                "version": int(r[0]),
                "status": r[1],
                "task_count": int(r[2] or 0),
                "created_at": _iso(r[3]),
                "published_at": _iso(r[4]),
            }
            for r in rows
        ]

    async def _lock_head(self, s: Any, tenant_id: str, dataset_id: str) -> tuple[int, str, Any]:
        """Lock the dataset row (serialises edits/publishes/pins); ``(head, status, tasks)``.

        The head version row is read by a SEPARATE statement after the lock is
        held: under READ COMMITTED it then sees what the previous lock holder
        committed (a join inside the locking statement would recheck only the
        locked row and read a stale version row).
        """
        from app.evals.dataset_versions import DatasetNotFoundError

        locked = (
            await s.execute(
                text(
                    "SELECT version, golden_tasks FROM ai_ops_datasets "
                    "WHERE tenant_id = :t AND id = :id FOR UPDATE"
                ),
                {"t": tenant_id, "id": dataset_id},
            )
        ).fetchone()
        if locked is None:
            raise DatasetNotFoundError(dataset_id)
        head = int(locked[0])
        row = (
            await s.execute(
                text(
                    "SELECT status, golden_tasks FROM ai_ops_dataset_versions "
                    "WHERE tenant_id = :t AND dataset_id = :id AND version = :v"
                ),
                {"t": tenant_id, "id": dataset_id, "v": head},
            )
        ).fetchone()
        if row is None:  # a dataset created before versioning: its head is published
            return head, "published", list(locked[1] or [])
        return head, str(row[0]), list(row[1] or [])

    async def edit_dataset(
        self,
        tenant_id: str,
        dataset_id: str,
        *,
        ops: list[dict[str, Any]],
        name: str | None = None,
        description: str | None = None,
        if_version: int | None = None,
    ) -> dict[str, Any]:
        """Edit the dataset; a published head gets a new draft version (never mutated)."""
        from app.evals.dataset_versions import (
            PUBLISHED,
            VersionConflictError,
            apply_edits,
        )

        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            head, status, tasks = await self._lock_head(s, tenant_id, dataset_id)
            if if_version is not None and int(if_version) != head:
                raise VersionConflictError(f"the head is version {head}, not {if_version}")
            if ops:
                new_tasks = apply_edits(tasks, ops)
                params = {"t": tenant_id, "id": dataset_id, "g": json.dumps(new_tasks)}
                if status == PUBLISHED:
                    head += 1
                    await s.execute(
                        text(
                            "INSERT INTO ai_ops_dataset_versions "
                            "(tenant_id, dataset_id, version, status, golden_tasks) "
                            "VALUES (:t, :id, :v, 'draft', CAST(:g AS jsonb))"
                        ),
                        {**params, "v": head},
                    )
                else:
                    await s.execute(
                        text(
                            "UPDATE ai_ops_dataset_versions "
                            "SET golden_tasks = CAST(:g AS jsonb) "
                            "WHERE tenant_id = :t AND dataset_id = :id AND version = :v "
                            "  AND status = 'draft'"
                        ),
                        {**params, "v": head},
                    )
                await s.execute(
                    text(
                        "UPDATE ai_ops_datasets SET version = :v, "
                        "golden_tasks = CAST(:g AS jsonb) WHERE tenant_id = :t AND id = :id"
                    ),
                    {**params, "v": head},
                )
            if name is not None or description is not None:
                await s.execute(
                    text(
                        "UPDATE ai_ops_datasets SET "
                        "name = COALESCE(CAST(:n AS text), name), "
                        "description = COALESCE(CAST(:d AS text), description) "
                        "WHERE tenant_id = :t AND id = :id"
                    ),
                    {"t": tenant_id, "id": dataset_id, "n": name, "d": description},
                )
            await s.commit()
        found = await self.get_dataset(tenant_id, dataset_id)
        if found is None:  # deleted concurrently
            from app.evals.dataset_versions import DatasetNotFoundError

            raise DatasetNotFoundError(dataset_id)
        return found

    async def _publish_locked(
        self, s: Any, tenant_id: str, dataset_id: str, version: int
    ) -> str | None:
        """Publish ``version`` if it is a draft; returns its prior status (None: missing)."""
        row = (
            await s.execute(
                text(
                    "SELECT status FROM ai_ops_dataset_versions "
                    "WHERE tenant_id = :t AND dataset_id = :id AND version = :v"
                ),
                {"t": tenant_id, "id": dataset_id, "v": int(version)},
            )
        ).fetchone()
        if row is None:
            return None
        if row[0] == "draft":
            await s.execute(
                text(
                    "UPDATE ai_ops_dataset_versions "
                    "SET status = 'published', published_at = NOW() "
                    "WHERE tenant_id = :t AND dataset_id = :id AND version = :v"
                ),
                {"t": tenant_id, "id": dataset_id, "v": int(version)},
            )
        return str(row[0])

    async def publish_version(
        self, tenant_id: str, dataset_id: str, version: int | None = None
    ) -> dict[str, Any]:
        from app.evals.dataset_versions import NothingToPublishError, VersionNotFoundError

        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            head, _status, _tasks = await self._lock_head(s, tenant_id, dataset_id)
            target = head if version is None else int(version)
            prior = await self._publish_locked(s, tenant_id, dataset_id, target)
            if prior is None:
                raise VersionNotFoundError(f"version {target} does not exist")
            if prior != "draft":
                raise NothingToPublishError(f"version {target} is already published")
            await s.commit()
        found = await self.get_dataset(tenant_id, dataset_id)
        if found is None:
            raise VersionNotFoundError(dataset_id)
        return found

    async def pin_version_for_run(
        self, tenant_id: str, dataset_id: str, version: int | None = None
    ) -> dict[str, Any]:
        """The published version a run executes; a draft is published first (frozen)."""
        from app.evals.dataset_versions import VersionNotFoundError

        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            head, _status, _tasks = await self._lock_head(s, tenant_id, dataset_id)
            target = head if version is None else int(version)
            if await self._publish_locked(s, tenant_id, dataset_id, target) is None:
                raise VersionNotFoundError(f"version {target} does not exist")
            await s.commit()
        pinned = await self.get_dataset_version(tenant_id, dataset_id, target)
        if pinned is None:
            raise VersionNotFoundError(f"version {target} does not exist")
        return pinned

    # ── eval results ────────────────────────────────────────────────────────
    async def add_eval_result(
        self, *, tenant_id: str, result_id: str, dataset_id: str, payload: dict[str, Any]
    ) -> None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO ai_ops_eval_results (id, tenant_id, dataset_id, payload) "
                    "VALUES (:id, :t, :d, CAST(:p AS jsonb))"
                ),
                {
                    "id": result_id,
                    "t": tenant_id,
                    "d": dataset_id,
                    "p": json.dumps(payload),
                },
            )
            await s.commit()

    async def update_eval_result(
        self, *, tenant_id: str, result_id: str, payload: dict[str, Any]
    ) -> None:
        """Replace a result's payload (a 202 run records ``running`` then its outcome)."""
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "UPDATE ai_ops_eval_results SET payload = CAST(:p AS jsonb) "
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {"id": result_id, "t": tenant_id, "p": json.dumps(payload)},
            )
            await s.commit()

    async def claim_run(
        self, tenant_id: str, result_id: str, owner: str, lease_seconds: float
    ) -> dict[str, Any] | None:
        """Lease an active run to one step (P7-1); None when another step holds it.

        Compare-and-set on the run row with the DB clock, so steps on any replica
        or worker agree: the lease is taken when it is free, expired, or already
        ``owner``'s. Returns the run's payload (with the lease) to work on.
        """
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(
                        "UPDATE ai_ops_eval_results SET payload = payload || "
                        "  jsonb_build_object('lease_owner', CAST(:o AS text), "
                        "    'lease_until', extract(epoch FROM clock_timestamp()) "
                        "                   + CAST(:lease AS float8)) "
                        "WHERE tenant_id = :t AND id = :id "
                        "  AND payload->>'status' IN ('queued', 'running') "
                        "  AND (COALESCE(CAST(payload->>'lease_until' AS float8), 0) "
                        "         < extract(epoch FROM clock_timestamp()) "
                        "       OR payload->>'lease_owner' = CAST(:o AS text)) "
                        "RETURNING payload"
                    ),
                    {"t": tenant_id, "id": result_id, "o": owner, "lease": float(lease_seconds)},
                )
            ).fetchone()
            await s.commit()
        return dict(row[0] or {}) if row is not None else None

    async def save_run(
        self,
        tenant_id: str,
        result_id: str,
        owner: str,
        payload: dict[str, Any],
        lease_seconds: float,
        *,
        release: bool = False,
    ) -> bool:
        """Write a run's progress, fenced on the lease (False: the lease was lost).

        Renews the lease, or frees it (``release``) when the step is done with
        the run.
        """
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(
                        "UPDATE ai_ops_eval_results SET payload = CAST(:p AS jsonb) || "
                        "  jsonb_build_object('lease_owner', CAST(:o AS text), "
                        "    'lease_until', CASE WHEN CAST(:release AS boolean) THEN 0 "
                        "      ELSE extract(epoch FROM clock_timestamp()) "
                        "           + CAST(:lease AS float8) END) "
                        "WHERE tenant_id = :t AND id = :id "
                        "  AND payload->>'lease_owner' = CAST(:o AS text) "
                        "RETURNING id"
                    ),
                    {
                        "t": tenant_id,
                        "id": result_id,
                        "o": owner,
                        "p": json.dumps(payload),
                        "lease": float(lease_seconds),
                        "release": bool(release),
                    },
                )
            ).fetchone()
            await s.commit()
        return row is not None

    async def get_eval_result(self, tenant_id: str, result_id: str) -> dict[str, Any] | None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(
                        "SELECT payload FROM ai_ops_eval_results "
                        "WHERE tenant_id = :t AND id = :id"
                    ),
                    {"t": tenant_id, "id": result_id},
                )
            ).fetchone()
        return dict(row[0] or {}) if row is not None else None

    async def list_eval_results(self, tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            rows = (
                await s.execute(
                    text(
                        "SELECT payload FROM ai_ops_eval_results WHERE tenant_id = :t "
                        "ORDER BY created_at DESC LIMIT :k"
                    ),
                    {"t": tenant_id, "k": limit},
                )
            ).fetchall()
        return [dict(r[0] or {}) for r in rows]

    async def count_eval_results(self, tenant_id: str) -> int:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            return int(
                (
                    await s.execute(
                        text("SELECT count(*) FROM ai_ops_eval_results WHERE tenant_id = :t"),
                        {"t": tenant_id},
                    )
                ).scalar_one()
            )

    # ── judges ──────────────────────────────────────────────────────────────
    async def create_judge(
        self, *, tenant_id: str, judge_id: str, payload: dict[str, Any]
    ) -> None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO ai_ops_judges (id, tenant_id, payload) "
                    "VALUES (:id, :t, CAST(:p AS jsonb))"
                ),
                {"id": judge_id, "t": tenant_id, "p": json.dumps(payload)},
            )
            await s.commit()

    async def get_judge(self, tenant_id: str, judge_id: str) -> dict[str, Any] | None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text("SELECT payload FROM ai_ops_judges WHERE tenant_id = :t AND id = :id"),
                    {"t": tenant_id, "id": judge_id},
                )
            ).fetchone()
        return dict(row[0] or {}) if row is not None else None

    # ── baselines ───────────────────────────────────────────────────────────
    async def set_baseline(self, *, tenant_id: str, metric_name: str, value: float) -> None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO ai_ops_baselines (tenant_id, metric_name, value) "
                    "VALUES (:t, :m, :v) "
                    "ON CONFLICT (tenant_id, metric_name) DO UPDATE "
                    "SET value = EXCLUDED.value, updated_at = NOW()"
                ),
                {"t": tenant_id, "m": metric_name, "v": float(value)},
            )
            await s.commit()

    async def set_baseline_if_absent(
        self, *, tenant_id: str, metric_name: str, value: float
    ) -> None:
        """First-run auto-baseline: never clobber an operator-set value."""
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO ai_ops_baselines (tenant_id, metric_name, value) "
                    "VALUES (:t, :m, :v) ON CONFLICT (tenant_id, metric_name) DO NOTHING"
                ),
                {"t": tenant_id, "m": metric_name, "v": float(value)},
            )
            await s.commit()

    async def get_baseline(self, tenant_id: str, metric_name: str) -> float | None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(
                        "SELECT value FROM ai_ops_baselines "
                        "WHERE tenant_id = :t AND metric_name = :m"
                    ),
                    {"t": tenant_id, "m": metric_name},
                )
            ).fetchone()
        return float(row[0]) if row is not None else None

    async def count_baselines(self, tenant_id: str) -> int:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            return int(
                (
                    await s.execute(
                        text("SELECT count(*) FROM ai_ops_baselines WHERE tenant_id = :t"),
                        {"t": tenant_id},
                    )
                ).scalar_one()
            )

    # ── drift alerts ────────────────────────────────────────────────────────
    async def add_alert(self, *, tenant_id: str, alert: dict[str, Any]) -> None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO ai_ops_drift_alerts "
                    "(id, tenant_id, severity, metric_name, payload) "
                    "VALUES (:id, :t, :sev, :m, CAST(:p AS jsonb))"
                ),
                {
                    "id": alert["alert_id"],
                    "t": tenant_id,
                    "sev": alert.get("severity", "info"),
                    "m": alert.get("metric_name", ""),
                    "p": json.dumps(alert),
                },
            )
            await s.commit()

    async def list_alerts(
        self, tenant_id: str, severity: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            rows = (
                await s.execute(
                    text(
                        # CAST(...) not `:sev::text` — see the bind-parameter note
                        # in TrustApprovalStore.list; asyncpg also cannot infer the
                        # type of a bare NULL bind.
                        "SELECT payload FROM ai_ops_drift_alerts WHERE tenant_id = :t "
                        "  AND (CAST(:sev AS text) IS NULL "
                        "       OR severity = CAST(:sev AS text)) "
                        "ORDER BY created_at DESC LIMIT :k"
                    ),
                    {"t": tenant_id, "sev": severity, "k": limit},
                )
            ).fetchall()
        return [dict(r[0] or {}) for r in rows]

    async def alert_severity_counts(self, tenant_id: str) -> dict[str, int]:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            rows = (
                await s.execute(
                    text(
                        "SELECT severity, count(*) FROM ai_ops_drift_alerts "
                        "WHERE tenant_id = :t GROUP BY severity"
                    ),
                    {"t": tenant_id},
                )
            ).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}


def _dataset_row(row: Any) -> dict[str, Any]:
    from app.evals.dataset_versions import public_dataset

    return public_dataset(
        {
            "dataset_id": row[0],
            "tenant_id": row[1],
            "name": row[2],
            "description": row[3],
            "golden_tasks": list(row[4] or []),
            "version": int(row[5]),
            "created_at": _iso(row[6]),
            "status": row[7],
            "published_version": int(row[8]) if row[8] is not None else None,
        }
    )
